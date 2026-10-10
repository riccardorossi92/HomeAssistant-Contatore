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

Il refresh_token che torna insieme all'access_token dura solo 30 minuti
(refresh_expires_in: 1800) - troppo poco per un coordinator a ciclo
giornaliero, quindi non viene nemmeno usato: si rifà login da zero a ogni
ciclo (vedi coordinator.py e const.py).
"""
from __future__ import annotations

import logging

import aiohttp

from .const import CLIENT_ID, TOKEN_URL

_LOGGER = logging.getLogger(__name__)


class IretiAuthError(Exception):
    """Errore generico di autenticazione."""


class IretiInvalidCredentials(IretiAuthError):
    """Username o password rifiutati da Keycloak (errore OAuth2 standard,
    tipicamente invalid_grant)."""


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
        dati = {
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": username,
            "password": password,
            "scope": "openid profile email",
        }

        # Il WAF davanti al token endpoint risponde 403 con una pagina HTML
        # se la richiesta porta i cookie lasciati dalle chiamate precedenti
        # sulla stessa sessione (JSESSIONID da /users/*, KEYCLOAK_LOCALE dal
        # login stesso): il primo login dopo l'avvio passa, tutti i successivi
        # no. Il login non ha bisogno di nessun cookie, quindi si riparte
        # sempre da un cookie jar vuoto (issue #10).
        self._session.cookie_jar.clear()

        try:
            async with self._session.post(TOKEN_URL, data=dati) as resp:
                try:
                    corpo = await resp.json(content_type=None)
                except ValueError:
                    # Risposta non JSON: tipicamente la pagina HTML del WAF.
                    testo = await resp.text()
                    raise IretiAuthError(
                        f"Login Ireti fallito (HTTP {resp.status}, risposta non JSON): "
                        f"{testo[:200]!r}"
                    ) from None
                if resp.status != 200:
                    errore = corpo.get("error") if isinstance(corpo, dict) else None
                    if errore in ("invalid_grant", "unauthorized_client"):
                        raise IretiInvalidCredentials(
                            corpo.get("error_description", errore)
                        )
                    raise IretiAuthError(
                        f"Login Ireti fallito (HTTP {resp.status}): {corpo!r}"
                    )
        except aiohttp.ClientError as err:
            raise IretiAuthError(f"Errore di trasporto durante il login Ireti: {err}") from err

        try:
            return corpo["access_token"]
        except (KeyError, TypeError) as err:
            raise IretiAuthError(
                f"Risposta di login inattesa (access_token mancante): {corpo!r}"
            ) from err
