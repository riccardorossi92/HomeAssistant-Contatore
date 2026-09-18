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
    seguire. La sessione va comunque riusata per le chiamate successive
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

        try:
            async with self._session.post(TOKEN_URL, data=dati) as resp:
                corpo = await resp.json(content_type=None)
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
