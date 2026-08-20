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
import json
import logging
import re
import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path
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


def _log_parsing_failure_context(html: str, campo_cercato: str) -> None:
    """Logga titolo + un'anteprima della pagina quando un campo atteso non
    si trova - serve a distinguere "regex sbagliato" (il campo c'e' ma in
    forma diversa) da "pagina completamente diversa da quella attesa"
    (es. errore, OTP saltato perche' il dispositivo e' gia' fidato, sessione
    scaduta) senza dover chiedere un'altra cattura per scoprirlo.

    Salva anche la pagina intera su disco (best-effort - se il filesystem
    non e' scrivibile in questo contesto, es. dentro il container di Home
    Assistant, non blocca nulla, si limita a non salvare)."""
    title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else "(nessun <title> trovato)"
    _LOGGER.error(
        "Campo '%s' non trovato. Titolo pagina: %r. Lunghezza: %d caratteri. "
        "Primi 300 caratteri: %r",
        campo_cercato,
        title,
        len(html),
        html[:300],
    )
    try:
        debug_path = Path("otp_page_debug.html")
        debug_path.write_text(html, encoding="utf-8")
        _LOGGER.error("Pagina completa salvata in %s", debug_path.resolve())
    except OSError as exc:
        _LOGGER.debug("Impossibile salvare la pagina di debug su disco: %s", exc)

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
        # Il parametro 'startURL' della pagina su cui atterriamo contiene un
        # token 'source=...' generato dal server al primo hop
        # (oauth2/authorize), che il server pretende di riavere indietro nel
        # campo 'startUrl' dentro 'message' per validare che il login
        # appartenga allo stesso flusso di autorizzazione - confermato
        # confrontando con una richiesta reale riuscita il 20/08/2026, dove
        # mandare un path nudo (senza questo token) produceva lo stesso
        # AuraClientInputException generico osservato più volte.
        start_url_value = resp.url.query.get(
            "startURL", "/PortaleClienti/setup/secur/RemoteAccessAuthorizationPage.apexp"
        )
        aura_page_uri = resp.url.path_qs
        referer = str(resp.url)
        resp.close()

        self._flow.fwuid = self._extract_fwuid(login_page_html)
        loaded = self._extract_loaded(login_page_html)
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

        # Costruito con json.dumps invece di concatenazione di stringhe:
        # oltre a essere più leggibile, evita JSON malformato se
        # email/password contenessero virgolette o backslash (mai
        # verificato prima, ma con tutti i problemi di "JSON non valido"
        # incontrati finora meglio non lasciarlo al caso).
        message = json.dumps({
            "actions": [{
                "id": "1;a",
                "descriptor": "apex://PED_LoginController/ACTION$loginUser",
                "callingDescriptor": "markup://c:PED_Login",
                "params": {
                    "username": email,
                    "password": password,
                    "startUrl": start_url_value,
                },
            }]
        })
        # 'loaded' NON puo' essere {} (vuoto): il server risponde con un
        # generico AuraClientInputException ("Unexpected request input")
        # se non corrisponde a quanto si aspetta - confermato confrontando
        # con una richiesta reale riuscita il 20/08/2026, dove conteneva un
        # riferimento alla versione del componente caricato
        # (es. {"APPLICATION@markup://siteforce:loginApp2":"1628_..."}).
        aura_context = json.dumps({
            "mode": "PROD",
            "fwuid": self._flow.fwuid,
            "app": "siteforce:loginApp2",
            "loaded": json.loads(loaded),
            "dn": [],
            "globals": {},
            "uad": True,
        })
        data = {
            "message": message,
            "aura.context": aura_context,
            "aura.pageURI": aura_page_uri,
            "aura.token": self._flow.aura_token,
        }

        # X-SFDC-Page-Scope-Id: mai visto in nessuna risposta del server in
        # tutta la HAR analizzata, solo nelle richieste - e' verosimilmente
        # generato lato client (un ID di correlazione per la pagina/sessione,
        # riusato identico su tutte le chiamate Aura), non qualcosa da
        # estrarre. Generato qui un UUID plausibile, non provato necessario
        # da solo ma coerente con quanto osservato in una richiesta reale.
        #
        # Origin/Referer/Content-Type aggiunti per la stessa ragione: non
        # individualmente confermati come causa del rifiuto, ma presenti
        # nella richiesta reale riuscita e a rischio zero da aggiungere.
        login_headers = {
            **headers,
            "X-SFDC-Page-Scope-Id": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://private.e-distribuzione.it",
            "Referer": referer,
        }

        async with self._session.post(
            f"{AURA_ENDPOINT}?r=2&other.PED_Login.loginUser=1",
            data=data,
            headers=login_headers,
        ) as resp:
            raw_text = await resp.text()
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError as err:
                _LOGGER.error(
                    "Risposta non-JSON da loginUser (status %s). Primi 500 "
                    "caratteri del corpo: %r",
                    resp.status,
                    raw_text[:500],
                )
                raise EdistribuzioneParsingError(
                    f"Risposta non-JSON da loginUser (status {resp.status}): "
                    f"{raw_text[:200]!r}"
                ) from err

        try:
            return_value = payload["actions"][0]["returnValue"]
        except (KeyError, IndexError) as err:
            raise EdistribuzioneParsingError(
                "Unexpected loginUser response shape"
            ) from err

        if not isinstance(return_value, str) or not return_value.startswith("OK:"):
            raise EdistribuzioneInvalidCredentials(str(return_value))

        frontdoor_url = return_value[len("OK:") :]

        # Following this establishes the `sid` session cookie e atterra su
        # una pagina-ponte che fa un redirect via JAVASCRIPT
        # (window.location.replace(...)) verso il vero form OTP - un
        # browser lo segue automaticamente, noi no. Confermato analizzando
        # l'ordine cronologico reale di una HAR il 20/08/2026: la pagina di
        # frontdoor.jsp NON e' mai il form OTP, e' solo un ponte.
        resp = await _get_following_redirects(self._session, frontdoor_url, headers=headers)
        bridge_html = await resp.text()
        resp.close()

        otp_form_url = self._extract_js_redirect_url(bridge_html)
        resp = await _get_following_redirects(self._session, otp_form_url, headers=headers)
        otp_page_html = await resp.text()
        resp.close()

        self._parse_otp_page(otp_page_html)
        await self._async_trigger_otp_send()

    async def _async_trigger_otp_send(self) -> None:
        """Il form OTP non parte da solo al caricamento della pagina: serve
        un primo submit del bottone, SENZA codice, per farlo davvero
        inviare - confermato su una HAR reale il 20/08/2026, la cui
        risposta contiene testualmente "Abbiamo inviato un codice a 5
        cifre al tuo indirizzo email" (non solo SMS, come si poteva
        pensare dal solo nome del campo OTP_Input).

        Il ViewState/CSRF nella risposta di QUESTA chiamata vanno poi usati
        per l'invio effettivo del codice (async_submit_otp), non quelli
        della pagina di atterraggio iniziale: sembrano ruotare ad ogni
        submit del form, quindi li aggiorniamo qui via lo stesso parser
        della pagina di atterraggio (la risposta e' ancora una pagina HTML
        completa con lo stesso form, solo con il messaggio di conferma e
        token freschi).
        """
        headers = {
            "User-Agent": _MOBILE_USER_AGENT,
            "Faces-Request": "partial/ajax",
        }
        data = {
            "AJAXREQUEST": "_viewRoot",
            "thePage:j_id2:i:f": "thePage:j_id2:i:f",
            "thePage:j_id2:i:f:pb:d:navigationType": "",
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

        self._parse_otp_page(body)

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

        # Pull the "Location" meta tag content directly.
        loc_match = re.search(r'name="Location"\s+content="([^"]+)"', body)
        if not loc_match:
            _log_parsing_failure_context(body, "meta Location dopo l'invio dell'OTP")
            try:
                Path("otp_submit_response_debug.html").write_text(body, encoding="utf-8")
                _LOGGER.error(
                    "Risposta completa salvata in %s",
                    Path("otp_submit_response_debug.html").resolve(),
                )
            except OSError as exc:
                _LOGGER.debug("Impossibile salvare la risposta di debug su disco: %s", exc)
            raise EdistribuzioneParsingError(
                "Redirect (meta Location) non trovato nella risposta dopo "
                f"l'invio dell'OTP (risposta lunga {len(body)} caratteri - "
                "vedi log per un'anteprima)"
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

    @staticmethod
    def _extract_loaded(html: str) -> str:
        """Estrae il valore grezzo (JSON, come stringa) del campo 'loaded'
        dallo stesso blob di bootstrap da cui si estrae fwuid - es.
        '{"APPLICATION@markup://siteforce:loginApp2":"1628_TW-..."}'.

        Necessario perche' aura.context con 'loaded':{} (vuoto) viene
        rifiutato dal server con un AuraClientInputException generico
        ("Unexpected request input... must be in the expected format"),
        confermato confrontando con una richiesta loginUser reale riuscita
        il 20/08/2026: il valore reale di 'loaded' e' non vuoto e va
        riportato identico.

        Ritorna '{}' (stringa) se non trovato, cosi' il chiamante degrada
        al comportamento precedente invece di fallire qui - il fallimento
        vero arrivera' comunque dal server sulla loginUser, con l'errore
        gia' diagnosticato da li'.
        """
        match = re.search(r'"loaded"\s*:\s*(\{[^}]*\})', html)
        if match:
            return match.group(1)

        match = re.search(r"%22loaded%22%3A(%7B.*?%7D)", html)
        if match:
            return unquote(match.group(1))

        return "{}"

    @staticmethod
    def _extract_js_redirect_url(html: str) -> str:
        """La pagina di frontdoor.jsp e' un ponte che rimanda al vero form
        OTP con `window.location.replace('URL')` (JavaScript, non un
        redirect HTTP) - un browser lo segue da solo, noi dobbiamo
        estrarre l'URL a mano e farci una GET esplicita.

        L'URL dentro replace() e' gia' assoluto e correttamente
        percent-encoded (e' dentro una stringa JS, non un attributo HTML),
        quindi nessun problema di doppia codifica come altrove in questo
        file - va usato cosi' com'e'.
        """
        match = re.search(r"window\.location\.replace\('([^']+)'\)", html)
        if match:
            return match.group(1)

        # Fallback: alcune varianti di questa pagina Salesforce usano
        # window.location.href invece di .replace(...).
        match = re.search(r"window\.location\.href\s*=\s*'([^']+)'", html)
        if match:
            return match.group(1)

        _log_parsing_failure_context(html, "window.location.replace(...) su pagina frontdoor")
        raise EdistribuzioneParsingError(
            "Redirect JavaScript verso il form OTP non trovato sulla pagina "
            f"di frontdoor (pagina lunga {len(html)} caratteri - vedi log "
            "per un'anteprima)"
        )

    def _parse_otp_page(self, html: str) -> None:
        def find(field_name: str) -> str:
            # Cerca l'intero tag <input ...> che contiene questo attributo
            # 'name', poi 'value' AL SUO INTERNO - indipendente dall'ordine
            # in cui i due attributi compaiono nel tag (il regex precedente
            # richiedeva 'name' prima di 'value', che potrebbe non essere
            # sempre vero).
            tag_match = re.search(
                rf'<input[^>]*name="{re.escape(field_name)}"[^>]*>', html
            )
            if not tag_match:
                _log_parsing_failure_context(html, field_name)
                raise EdistribuzioneParsingError(
                    f"Campo '{field_name}' non trovato sulla pagina OTP "
                    f"(pagina lunga {len(html)} caratteri - vedi log per un'anteprima)"
                )
            value_match = re.search(r'value="([^"]*)"', tag_match.group(0))
            if not value_match:
                raise EdistribuzioneParsingError(
                    f"Campo '{field_name}' trovato ma senza attributo 'value' "
                    f"leggibile: {tag_match.group(0)!r}"
                )
            return value_match.group(1)

        self._flow.view_state = find("com.salesforce.visualforce.ViewState")
        self._flow.view_state_version = find("com.salesforce.visualforce.ViewStateVersion")
        self._flow.view_state_mac = find("com.salesforce.visualforce.ViewStateMAC")
        self._flow.view_state_csrf = find("com.salesforce.visualforce.ViewStateCSRF")
        # Field name prefixes are Salesforce-generated and may shift between
        # deploys (`thePage:j_id2:...`) - try to discover them dynamically
        # rather than trusting the hardcoded defaults used as a fallback above.
        otp_field = re.search(r'name="([^"]*OTP_Input[^"]*)"', html)
        if otp_field:
            self._flow.otp_field_name = otp_field.group(1)
        # La pagina ha DUE campi con "Richiedi_nuovo_OTP" nel nome: una
        # checkbox visibile (element___input____...) e un campo nascosto
        # che la specchia (element___hidden____...), aggiornato da un
        # onclick JS - e' quest'ultimo che va sottomesso. Un regex generico
        # trova per primo la checkbox (appare prima nell'HTML), causando
        # l'invio del campo sbagliato - confermato su una risposta reale
        # il 20/08/2026, dove il server trattava la sottomissione del
        # codice come un'ennesima richiesta di reinvio invece che una
        # convalida.
        resend_field = re.search(
            r'name="([^"]*element___hidden____Richiedi_nuovo_OTP[^"]*)"', html
        )
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
