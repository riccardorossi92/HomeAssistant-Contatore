"""Authentication client for e-Distribuzione (private.e-distribuzione.it).

The login itself is a standard Salesforce OAuth2 Authorization Code flow with
PKCE. The friction is in the middle: credentials are submitted via an Aura
("Lightning") remote action, and the OTP step is a classic Salesforce
Visualforce "Login Flow" interview page using ViewState/RichFaces AJAX.

Both of those are UI implementation details, not a stable public API, so this
module scrapes HTML/JSON fragments with regexes. It WILL break if Enel changes
their Experience Cloud template, RichFaces version, or field API names.
Treat everything in `_extract_*` as the first thing to check if login starts
failing.

Once we have a refresh_token, none of this fragile code needs to run again:
async_refresh_access_token() only talks to the standard OAuth2 token endpoint.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from .const import (
    AURA_ENDPOINT,
    LOGIN_PAGE_URL,
    LOGINFLOW_URL,
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPE,
    OAUTH_TOKEN_URL,
    OAUTH_USERINFO_URL,
)

_LOGGER = logging.getLogger(__name__)

_MOBILE_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"

_MAX_REDIRECT_HOPS = 15


class EdistribuzioneAuthError(Exception):
    """Generic authentication failure."""


class EdistribuzioneInvalidCredentials(EdistribuzioneAuthError):
    """Wrong email/password."""


class EdistribuzioneInvalidOtp(EdistribuzioneAuthError):
    """Wrong or expired OTP code."""


class EdistribuzioneParsingError(EdistribuzioneAuthError):
    """The scraped page didn't contain what we expected.

    Most likely cause: Enel changed something in their Salesforce site and
    the regexes below need updating. Capture a fresh HAR and compare.
    """


async def _get_following_redirects(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
) -> aiohttp.ClientResponse:
    """GET seguendo i redirect a mano invece di allow_redirects=True.

    Necessario perche' la catena di redirect di /services/oauth2/authorize
    passa per un parametro 'startURL' che contiene un URL annidato
    percent-encoded (%2F, %3F, ...). Lasciando che aiohttp gestisca i
    redirect da solo (allow_redirects=True), il comportamento dipende
    dall'opzione 'requote_redirect_url' della ClientSession (che puo'
    ri-codificare il Location gia' codificato, alterandolo ad ogni hop) e
    puo' entrare in un loop infinito fino a TooManyRedirects - riprodotto e
    confermato il 20/08/2026 con un HAR reale. Seguendo i redirect
    esplicitamente con encoded=True sull'URL del prossimo hop, il valore
    del Location non viene mai ri-processato: la stessa catena che con
    allow_redirects=True va in loop qui si risolve in 3 hop.

    Ritorna la risposta finale (status < 300, nessun altro Location);
    il chiamante e' responsabile di chiudere/consumare 'resp' come al solito.
    """
    resp = await session.get(url, params=params, headers=headers, allow_redirects=False)

    for _ in range(_MAX_REDIRECT_HOPS):
        location = resp.headers.get("Location")
        if location is None:
            return resp

        resp.close()
        location_url = aiohttp.client.URL(location, encoded=True)
        next_url = location_url if location_url.is_absolute() else resp.url.join(location_url)
        resp = await session.get(next_url, headers=headers, allow_redirects=False)

    resp.close()
    raise EdistribuzioneAuthError(
        f"Troppi redirect (>{_MAX_REDIRECT_HOPS}) seguendo {url}: possibile "
        "cambiamento lato Salesforce nella struttura dei redirect di login."
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@dataclass
class _LoginFlowState:
    """Everything we need to carry between the OTP page and its submission."""

    fwuid: str | None = None
    aura_token: str | None = None
    view_state: str | None = None
    view_state_version: str | None = None
    view_state_mac: str | None = None
    view_state_csrf: str | None = None
    form_action_url: str | None = None
    # Fields captured under `thePage:j_id2:i:f:...` vary per deploy; we keep
    # the raw prefix so we don't have to hardcode Salesforce's generated IDs.
    otp_field_name: str | None = None
    resend_field_name: str | None = None
    next_field_name: str | None = None


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str
    instance_url: str
    id_token: str | None = None
    raw: dict = field(default_factory=dict)


class EdistribuzioneAuthClient:
    """Drives the login -> OTP -> OAuth code -> token exchange sequence."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._code_verifier: str | None = None
        self._oauth_state: str | None = None
        self._flow = _LoginFlowState()

    # -- Step 1: authorize + credentials -------------------------------------

    async def async_begin_login(self, email: str, password: str) -> None:
        """Submit email/password. Raises if credentials are rejected.

        On success, internal state is primed so that async_submit_otp() can
        complete the flow. This always assumes an OTP step follows, matching
        every capture we've seen so far.
        """
        self._code_verifier, code_challenge = _make_pkce_pair()
        self._oauth_state = _b64url(secrets.token_bytes(16))

        headers = {"User-Agent": _MOBILE_USER_AGENT}
        params = {
            "prompt": "login",
            "nonce": _b64url(secrets.token_bytes(16)),
            "display": "touch",
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "login_hint": email,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "redirect_uri": OAUTH_REDIRECT_URI,
            "client_id": OAUTH_CLIENT_ID,
            "state": self._oauth_state,
        }

        # Segue i redirect a mano (vedi _get_following_redirects) invece di
        # allow_redirects=True: quest'ultimo puo' entrare in un loop
        # infinito su questa catena specifica per via di un parametro
        # ('startURL') che contiene un URL annidato gia' percent-encoded.
        resp = await _get_following_redirects(
            self._session, OAUTH_AUTHORIZE_URL, params=params, headers=headers
        )
        login_page_html = await resp.text()
        resp.close()

        self._flow.fwuid = self._extract_fwuid(login_page_html)
        # aura.token: confermato su una HAR reale con login riuscito il
        # 20/08/2026 che il client invia letteralmente la stringa "null"
        # (non un token vero) per questa azione specifica, e il server la
        # accetta ("state":"SUCCESS"). Le due ipotesi precedenti (token nel
        # body HTML, poi token in un cookie __Host-ERIC_PROD*) erano
        # entrambe sbagliate - quel cookie non compare da nessuna parte in
        # una sessione fresca. Verosimilmente non c'e' ancora una sessione
        # autenticata da proteggere via CSRF a questo punto del flusso,
        # quindi il server non pretende un token reale per loginUser.
        self._flow.aura_token = "null"

        message = (
            '{"actions":[{"id":"1;a","descriptor":'
            '"apex://PED_LoginController/ACTION$loginUser",'
            '"callingDescriptor":"markup://c:PED_Login","params":'
            f'{{"username":"{email}","password":"{password}",'
            '"startUrl":"/PortaleClienti/setup/secur/RemoteAccessAuthorizationPage.apexp"}}}}]}'
        )
        aura_context = (
            '{"mode":"PROD","fwuid":"' + self._flow.fwuid + '",'
            '"app":"siteforce:loginApp2","loaded":{},"dn":[],"globals":{},"uad":true}'
        )
        data = {
            "message": message,
            "aura.context": aura_context,
            "aura.pageURI": "/PortaleClienti/s/login/",
            "aura.token": self._flow.aura_token,
        }

        async with self._session.post(
            f"{AURA_ENDPOINT}?r=2&other.PED_Login.loginUser=1",
            data=data,
            headers=headers,
        ) as resp:
            payload = await resp.json(content_type=None)

        try:
            return_value = payload["actions"][0]["returnValue"]
        except (KeyError, IndexError) as err:
            raise EdistribuzioneParsingError(
                "Unexpected loginUser response shape"
            ) from err

        if not isinstance(return_value, str) or not return_value.startswith("OK:"):
            raise EdistribuzioneInvalidCredentials(str(return_value))

        frontdoor_url = return_value[len("OK:") :]

        # Following this establishes the `sid` session cookie and lands on the
        # OTP interview page (loginflow.apexp). Stesso rischio di loop del
        # commento sopra su OAUTH_AUTHORIZE_URL: frontdoor_url ha tipicamente
        # un parametro retURL anch'esso percent-encoded con un URL annidato.
        resp = await _get_following_redirects(self._session, frontdoor_url, headers=headers)
        otp_page_html = await resp.text()
        resp.close()

        self._parse_otp_page(otp_page_html)

    # -- Step 2: OTP ----------------------------------------------------------

    async def async_submit_otp(self, otp_code: str) -> OAuthTokens:
        """Submit the OTP and complete the OAuth2 code exchange."""
        if self._flow.view_state is None:
            raise EdistribuzioneAuthError(
                "async_begin_login() must succeed before async_submit_otp()"
            )

        headers = {
            "User-Agent": _MOBILE_USER_AGENT,
            "Faces-Request": "partial/ajax",
        }
        data = {
            "AJAXREQUEST": "_viewRoot",
            "thePage:j_id2:i:f": "thePage:j_id2:i:f",
            "thePage:j_id2:i:f:pb:d:navigationType": "",
            (self._flow.otp_field_name or "thePage:j_id2:i:f:pb:d:element___input____OTP_Input"): otp_code,
            (self._flow.resend_field_name or "thePage:j_id2:i:f:pb:d:element___hidden____Richiedi_nuovo_OTP"): "false",
            "com.salesforce.visualforce.ViewState": self._flow.view_state,
            "com.salesforce.visualforce.ViewStateVersion": self._flow.view_state_version,
            "com.salesforce.visualforce.ViewStateMAC": self._flow.view_state_mac,
            "com.salesforce.visualforce.ViewStateCSRF": self._flow.view_state_csrf,
            (self._flow.next_field_name or "thePage:j_id2:i:f:pb:pbb:nextAjax"): (
                self._flow.next_field_name or "thePage:j_id2:i:f:pb:pbb:nextAjax"
            ),
        }

        url = self._flow.form_action_url or f"{LOGINFLOW_URL}?sfdcIFrameOrigin=null"
        async with self._session.post(url, data=data, headers=headers) as resp:
            body = await resp.text()

        if "Codice OTP" in body and "errato" in body.lower():
            raise EdistribuzioneInvalidOtp(body[:500])

        redirect_match = re.search(r'content="([^"]*Location[^"]*)"', body) or re.search(
            r'Location["\']?\s*content=["\']([^"\']+)', body
        )
        # Simpler/more robust: pull the "Location" meta tag content directly.
        loc_match = re.search(r'name="Location"\s+content="([^"]+)"', body)
        if not loc_match:
            raise EdistribuzioneParsingError(
                "Could not find post-OTP redirect Location in loginFlow response"
            )
        next_url = unquote(loc_match.group(1))
        if next_url.startswith("/"):
            next_url = "https://private.e-distribuzione.it" + next_url

        # This page normally triggers a JS redirect to eneldist://redirect?code=...
        # We can't follow a custom URL scheme with aiohttp, so scrape the code
        # and state straight out of the returned HTML/JS instead of navigating.
        async with self._session.get(next_url, headers=headers) as resp:
            consent_html = await resp.text()

        code_match = re.search(r"[?&]code=([^&'\"]+)", consent_html)
        state_match = re.search(r"[?&]state=([^&'\"]+)", consent_html)
        if not code_match:
            raise EdistribuzioneParsingError(
                "Could not find authorization code in consent page response"
            )
        auth_code = unquote(code_match.group(1))

        return await self._async_exchange_code(auth_code)

    # -- Token exchange / refresh ---------------------------------------------

    async def _async_exchange_code(self, code: str) -> OAuthTokens:
        data = {
            "code": code,
            "code_verifier": self._code_verifier,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "authorization_code",
        }
        async with self._session.post(OAUTH_TOKEN_URL, data=data) as resp:
            payload = await resp.json(content_type=None)
        return self._tokens_from_payload(payload)

    async def async_refresh_access_token(self, refresh_token: str) -> OAuthTokens:
        """Get a fresh access_token. No Aura/OTP involved - this is the path
        the integration should use on every normal startup/renewal."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        }
        async with self._session.post(OAUTH_TOKEN_URL, data=data) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise EdistribuzioneAuthError(
                    f"refresh_token exchange failed ({resp.status}): {text[:300]}"
                )
            payload = await resp.json(content_type=None)
        # Salesforce doesn't always return a new refresh_token on refresh -
        # keep the old one if a new one isn't present.
        payload.setdefault("refresh_token", refresh_token)
        return self._tokens_from_payload(payload)

    @staticmethod
    def _tokens_from_payload(payload: dict) -> OAuthTokens:
        try:
            return OAuthTokens(
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                instance_url=payload.get("instance_url", ""),
                id_token=payload.get("id_token"),
                raw=payload,
            )
        except KeyError as err:
            raise EdistribuzioneParsingError(
                f"Token endpoint response missing expected field: {err}"
            ) from err

    # -- HTML/JSON scraping helpers --------------------------------------------
    # These are the parts most likely to need fixing against a fresh HAR.

    @staticmethod
    def _extract_fwuid(html: str) -> str:
        # Forma diretta: "fwuid":"<value>" letterale in JSON/JS embedded.
        match = re.search(r'"fwuid"\s*:\s*"([^"]+)"', html)
        if match:
            return match.group(1)

        # Forma percent-encoded: confermato su cattura reale il 20/08/2026,
        # il blob di contesto Aura e' incorporato direttamente nel PATH
        # dell'URL di uno <script src="...">, non in una variabile JS:
        #   <script src="/PortaleClienti/s/sfsites/l/%7B%22mode%22...
        #     %22fwuid%22%3A%22<value>%22...%7D/app.js">
        # Deve essere cosi' perche' virgolette letterali dentro un
        # attributo src="..." romperebbero il parsing HTML.
        match = re.search(r"%22fwuid%22%3A%22([^%]+)%22", html)
        if match:
            return match.group(1)

        raise EdistribuzioneParsingError("fwuid not found on login page")

    def _parse_otp_page(self, html: str) -> None:
        def find(pattern: str, name: str) -> str:
            m = re.search(pattern, html)
            if not m:
                raise EdistribuzioneParsingError(f"{name} not found on OTP page")
            return m.group(1)

        self._flow.view_state = find(
            r'name="com\.salesforce\.visualforce\.ViewState"\s+[^>]*value="([^"]+)"',
            "ViewState",
        )
        self._flow.view_state_version = find(
            r'name="com\.salesforce\.visualforce\.ViewStateVersion"\s+[^>]*value="([^"]+)"',
            "ViewStateVersion",
        )
        self._flow.view_state_mac = find(
            r'name="com\.salesforce\.visualforce\.ViewStateMAC"\s+[^>]*value="([^"]+)"',
            "ViewStateMAC",
        )
        self._flow.view_state_csrf = find(
            r'name="com\.salesforce\.visualforce\.ViewStateCSRF"\s+[^>]*value="([^"]+)"',
            "ViewStateCSRF",
        )
        # Field name prefixes are Salesforce-generated and may shift between
        # deploys (`thePage:j_id2:...`) - try to discover them dynamically
        # rather than trusting the hardcoded defaults used as a fallback above.
        otp_field = re.search(r'name="([^"]*OTP_Input[^"]*)"', html)
        if otp_field:
            self._flow.otp_field_name = otp_field.group(1)
        resend_field = re.search(r'name="([^"]*Richiedi_nuovo_OTP[^"]*)"', html)
        if resend_field:
            self._flow.resend_field_name = resend_field.group(1)
        next_field = re.search(r'name="([^"]*nextAjax[^"]*)"', html)
        if next_field:
            self._flow.next_field_name = next_field.group(1)

        form_action = re.search(r'<form[^>]+action="([^"]+)"', html)
        if form_action:
            action = form_action.group(1)
            self._flow.form_action_url = (
                action if action.startswith("http") else f"https://private.e-distribuzione.it{action}"
            )
