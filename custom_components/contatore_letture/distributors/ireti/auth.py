"""Login Ireti (smartpod.ireti.it): password grant Keycloak diretto.

Una sola richiesta, niente scraping di form, niente OTP, niente PKCE -
verificato su cattura reale il 31/08/2026 (vedi
documentation/protocols/ireti-protocol.md, sezione "Login"):

    POST {TOKEN_URL}
      grant_type=password
      client_id=SmartPOD-Angular
      username=<utente>
      password=<password>
      scope=openid profile email

Se il WAF davanti al token endpoint blocca la richiesta (403 con pagina HTML,
tipicamente perche' la password contiene caratteri come "<", ">" o "/*" che
le sue regole scambiano per XSS/SQL injection - issue #21), si ripiega sul
flusso del browser: form di login di Keycloak + authorization_code con PKCE.
Il form non e' filtrato allo stesso modo e lo scambio del code non contiene
la password.

Il refresh_token che torna insieme all'access_token dura solo 30 minuti
(refresh_expires_in: 1800) - troppo poco per un coordinator a ciclo
giornaliero, quindi non viene nemmeno usato: si rifà login da zero a ogni
ciclo (vedi coordinator.py e const.py).
"""
from __future__ import annotations

import base64
import hashlib
import html
import logging
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import aiohttp

from .const import (
    AUTH_URL,
    BASE_URL,
    CLIENT_ID,
    HEADERS_PAGINA,
    HOST_LOGIN_AMMESSI,
    REDIRECT_URI,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)


class IretiAuthError(Exception):
    """Errore generico di autenticazione."""


class IretiInvalidCredentials(IretiAuthError):
    """Username o password rifiutati da Keycloak (errore OAuth2 standard,
    tipicamente invalid_grant)."""


class IretiHostNonAmmesso(IretiAuthError):
    """L'action del form di login punta fuori da HOST_LOGIN_AMMESSI (o non e'
    https): le credenziali non vengono mandate."""


def _host_ammesso(host: str) -> bool:
    """True se l'host e' in HOST_LOGIN_AMMESSI, o ne e' un sottodominio per
    le voci che iniziano con "."."""
    return any(
        host.endswith(voce) if voce.startswith(".") else host == voce
        for voce in HOST_LOGIN_AMMESSI
    )


def _verifica_host(url: str, passo: str) -> None:
    """Alza IretiHostNonAmmesso se l'URL non e' https su un host di
    HOST_LOGIN_AMMESSI. Log ed errore dicono quale host e a che passo del
    login, come per E-Distribuzione e Areti; nel messaggio solo schema e
    host, perche' il resto dell'URL contiene il codice di sessione di
    Keycloak."""
    parti = urlsplit(url)
    schema = parti.scheme.lower()
    host = (parti.hostname or "").lower()
    if schema == "https" and _host_ammesso(host):
        return
    if not schema:
        motivo = "e' un indirizzo relativo, non previsto a questo passo"
    elif schema != "https":
        motivo = "non usa https"
    else:
        motivo = "non e' tra quelli ammessi"
    messaggio = (
        f"Accesso Ireti interrotto al passo \"{passo}\": il portale ha indicato "
        f"{schema}://{host}, che {motivo}. Se e' un host legittimo del portale va "
        "aggiunto a HOST_LOGIN_AMMESSI in distributors/ireti/const.py."
    )
    _LOGGER.error(messaggio)
    raise IretiHostNonAmmesso(messaggio)


class _IretiWafBlock(IretiAuthError):
    """Il WAF ha risposto 403 con una pagina HTML invece del JSON di Keycloak.
    Interna: async_login la intercetta per ripiegare sul flusso a form."""


# <form ... action="...login-actions/authenticate?...">: l'ordine degli
# attributi non e' garantito, quindi si cerca prima il tag e poi l'action.
_RE_FORM = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
_RE_ACTION = re.compile(r'\baction="([^"]+)"', re.IGNORECASE)


class IretiAuthClient:
    """Esegue il login e ritorna l'access_token.

    Non serve una sessione con cookie jar dedicata (a differenza di areti/
    edistribuzione): è un client OAuth2 diretto, nessun redirect da
    seguire, e anzi il cookie jar va svuotato prima di ogni login (vedi
    async_login). La sessione va comunque riusata per le chiamate successive
    (api.py) perché porta gli stessi header "da browser" necessari per
    non finire in timeout contro il WAF di /users/* e /readings/*."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_login(self, username: str, password: str) -> str:
        """Ritorna l'access_token. Solleva IretiInvalidCredentials su
        credenziali errate, IretiAuthError per qualunque altro problema
        (rete, risposta inattesa)."""
        # Il WAF davanti al token endpoint risponde 403 con una pagina HTML
        # se la richiesta porta i cookie lasciati dalle chiamate precedenti
        # sulla stessa sessione (JSESSIONID da /users/*, KEYCLOAK_LOCALE dal
        # login stesso): il primo login dopo l'avvio passa, tutti i successivi
        # no. Il login non ha bisogno di nessun cookie, quindi si riparte
        # sempre da un cookie jar vuoto (issue #10).
        self._session.cookie_jar.clear()

        try:
            try:
                return await self._login_password_grant(username, password)
            except _IretiWafBlock as blocco:
                # Stessa pagina 403 anche con il jar vuoto: il WAF non
                # gradisce qualcosa nel corpo (issue #21: password con
                # caratteri speciali). Il form del browser non e' filtrato.
                _LOGGER.info(
                    "Login Ireti bloccato dal WAF (%s): riprovo con il flusso "
                    "a form del browser",
                    blocco,
                )
                self._session.cookie_jar.clear()
                try:
                    return await self._login_authorization_code(username, password)
                finally:
                    # Cookie di Keycloak (AUTH_SESSION_ID...) che non servono
                    # alle chiamate successive e farebbero scattare il 403.
                    self._session.cookie_jar.clear()
        except aiohttp.ClientError as err:
            raise IretiAuthError(f"Errore di trasporto durante il login Ireti: {err}") from err

    async def _richiedi_token(self, dati: dict[str, str]) -> str:
        """POST al token endpoint e ritorna l'access_token."""
        async with self._session.post(TOKEN_URL, data=dati) as resp:
            try:
                corpo = await resp.json(content_type=None)
            except ValueError:
                # Risposta non JSON: tipicamente la pagina HTML del WAF.
                testo = await resp.text()
                messaggio = (
                    f"Login Ireti fallito (HTTP {resp.status}, risposta non JSON): "
                    f"{testo[:200]!r}"
                )
                if resp.status == 403:
                    raise _IretiWafBlock(messaggio) from None
                raise IretiAuthError(messaggio) from None
            if resp.status != 200:
                errore = corpo.get("error") if isinstance(corpo, dict) else None
                if errore in ("invalid_grant", "unauthorized_client"):
                    raise IretiInvalidCredentials(corpo.get("error_description", errore))
                raise IretiAuthError(f"Login Ireti fallito (HTTP {resp.status}): {corpo!r}")

        try:
            return corpo["access_token"]
        except (KeyError, TypeError) as err:
            raise IretiAuthError(
                f"Risposta di login inattesa (access_token mancante): {corpo!r}"
            ) from err

    async def _login_password_grant(self, username: str, password: str) -> str:
        return await self._richiedi_token(
            {
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "username": username,
                "password": password,
                "scope": "openid profile email",
            }
        )

    async def _login_authorization_code(self, username: str, password: str) -> str:
        """Flusso del browser: GET pagina di login -> POST del form (302 con
        code) -> scambio del code. PKCE S256 come fa l'app Angular."""
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        state = secrets.token_urlsafe(16)
        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "nonce": secrets.token_urlsafe(16),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        headers_pagina = {**HEADERS_PAGINA}

        async with self._session.get(AUTH_URL, params=params, headers=headers_pagina) as resp:
            if resp.status != 200:
                raise IretiAuthError(
                    f"Pagina di login Ireti non disponibile (HTTP {resp.status})"
                )
            pagina = await resp.text()

        action = self._trova_action_login(pagina)

        async with self._session.post(
            action,
            data={"username": username, "password": password},
            headers={**headers_pagina, "Origin": BASE_URL, "Referer": str(resp.url)},
            allow_redirects=False,
        ) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
            elif resp.status == 200 and "kc-form-login" in await resp.text():
                # Keycloak ripropone il form con il messaggio di errore.
                raise IretiInvalidCredentials("Invalid username or password")
            else:
                raise IretiAuthError(
                    f"Login Ireti a form fallito (HTTP {resp.status})"
                )

        if not location.startswith(REDIRECT_URI):
            raise IretiAuthError(f"Redirect di login inatteso: {location[:200]!r}")
        query = parse_qs(urlsplit(location).query)
        if query.get("state", [None])[0] != state:
            raise IretiAuthError("Login Ireti a form: state non corrispondente")
        if "code" not in query:
            raise IretiAuthError(
                f"Login Ireti a form senza code (errore: {query.get('error')})"
            )

        # Il WAF rifiuta il token endpoint se la richiesta porta i cookie
        # impostati da Keycloak durante il form (vedi async_login).
        self._session.cookie_jar.clear()
        return await self._richiedi_token(
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": query["code"][0],
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            }
        )

    @staticmethod
    def _trova_action_login(pagina: str) -> str:
        for tag in _RE_FORM.findall(pagina):
            trovata = _RE_ACTION.search(tag)
            if trovata and "login-actions/authenticate" in trovata.group(1):
                action = html.unescape(trovata.group(1))
                # Le credenziali vanno solo al portale, mai altrove.
                _verifica_host(action, "form di login Keycloak")
                return action
        raise IretiAuthError("Form di login Ireti non trovato nella pagina")
