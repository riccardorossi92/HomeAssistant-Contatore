"""Login SET Distribuzione: Azure AD B2C, Authorization Code + PKCE.

Niente password grant diretto (come Ireti): bisogna passare dalla pagina
di login ospitata su b2clogin.com, come fa MSAL.js nel browser. Sequenza
verificata su cattura reale (13/10/2026, vedi
documentation/protocols/set-distribuzione-protocol.md, sezione "Login"):

    1. GET  {AUTHORIZE_URL}?client_id=...&code_challenge=...  -> pagina HTML
       con il blob 'var SETTINGS = {...}' (csrf + transId) e i cookie
       x-ms-cpim-* della transazione
    2. POST {SELF_ASSERTED_URL}?tx=...&p=B2C_1A_signin
       request_type=RESPONSE&signInName=<email>&password=<password>
       (header X-CSRF-TOKEN) -> {"status": "200"} se le credenziali vanno
    3. GET  {CONFIRMED_URL}?rememberMe=false&csrf_token=...&tx=...
       -> 302 verso REDIRECT_URI con 'code' nel fragment
    4. POST {TOKEN_URL} grant_type=authorization_code + code_verifier
       (con Origin del portale, vedi HEADERS_ORIGINE_PORTALE in const.py)

Nessun OTP. Lo stesso schema è stato provato dal vivo su AcegasApsAmga
(scripts/verify_acegasapsamga_login.py), altro tenant B2C.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import aiohttp

from .const import (
    AUTHORIZE_URL,
    B2C_POLICY,
    CLIENT_ID,
    CONFIRMED_URL,
    HEADERS_BROWSER,
    HEADERS_ORIGINE_PORTALE,
    REDIRECT_URI,
    SCOPE,
    SELF_ASSERTED_URL,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

_RE_SETTINGS = re.compile(r"var SETTINGS = (\{.*?\});", re.S)


class SetDistribuzioneAuthError(Exception):
    """Errore generico di autenticazione."""


class SetDistribuzioneInvalidCredentials(SetDistribuzioneAuthError):
    """Email o password rifiutate dalla pagina di login B2C."""


def _b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode("ascii")


def genera_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge S256)."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def estrai_settings(html: str) -> tuple[str, str]:
    """(csrf, transId) dal blob SETTINGS della pagina di login B2C."""
    trovato = _RE_SETTINGS.search(html)
    if not trovato:
        raise SetDistribuzioneAuthError(
            "Blob SETTINGS non trovato nella pagina di login B2C (formato cambiato?)"
        )
    try:
        settings = json.loads(trovato.group(1))
        return settings["csrf"], settings["transId"]
    except (ValueError, KeyError, TypeError) as err:
        raise SetDistribuzioneAuthError(
            f"Blob SETTINGS della pagina di login B2C non leggibile: {err}"
        ) from err


def parametri_redirect(location: str) -> dict[str, str]:
    """Parametri del redirect finale verso il portale: B2C li mette nel
    fragment (response_mode=fragment), con un ripiego sulla query."""
    parti = urlsplit(location)
    grezzi = parse_qs(parti.fragment) or parse_qs(parti.query)
    return {chiave: valori[0] for chiave, valori in grezzi.items() if valori}


def nuovo_cookie_jar() -> aiohttp.CookieJar:
    """Cookie jar per la sessione del login.

    quote_cookie=False è necessario: i valori dei cookie x-ms-cpim-*
    contengono '/', '+' e '=', e aiohttp di default li rimanda tra
    virgolette. B2C non li riconosce più e risponde 400 "Bad Request" al
    passo 2 (verificato dal vivo il 13/10/2026; senza virgolette la stessa
    richiesta riceve la normale risposta JSON)."""
    return aiohttp.CookieJar(quote_cookie=False)


class SetDistribuzioneAuthClient:
    """Esegue il login e ritorna l'access_token.

    La sessione deve avere un cookie jar proprio creato con
    nuovo_cookie_jar(): i cookie x-ms-cpim-* impostati al passo 1 servono
    ai passi 2 e 3. Viene svuotato a ogni login, così un cookie di SSO
    rimasto da un login precedente non cambia il flusso (la pagina di
    login va sempre mostrata)."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_login(self, email: str, password: str) -> str:
        """Ritorna l'access_token. Solleva SetDistribuzioneInvalidCredentials
        su credenziali errate, SetDistribuzioneAuthError per qualunque altro
        problema (rete, risposta inattesa)."""
        self._session.cookie_jar.clear()
        verifier, challenge = genera_pkce()
        try:
            csrf, trans_id = await self._async_pagina_login(challenge)
            await self._async_invia_credenziali(csrf, trans_id, email, password)
            code = await self._async_code(csrf, trans_id)
            return await self._async_scambia_code(code, verifier)
        except aiohttp.ClientError as err:
            raise SetDistribuzioneAuthError(
                f"Errore di trasporto durante il login SET Distribuzione: {err}"
            ) from err

    async def _async_pagina_login(self, challenge: str) -> tuple[str, str]:
        parametri = {
            "client_id": CLIENT_ID,
            "scope": SCOPE,
            "redirect_uri": REDIRECT_URI,
            "response_mode": "fragment",
            "response_type": "code",
            "client_info": "1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": secrets.token_hex(16),
            "state": _b64url(secrets.token_bytes(16)),
        }
        async with self._session.get(
            AUTHORIZE_URL, params=parametri, headers=HEADERS_BROWSER
        ) as resp:
            testo = await resp.text()
            if resp.status != 200:
                raise SetDistribuzioneAuthError(
                    f"Pagina di login B2C: HTTP {resp.status}: {testo[:200]!r}"
                )
        return estrai_settings(testo)

    async def _async_invia_credenziali(
        self, csrf: str, trans_id: str, email: str, password: str
    ) -> None:
        async with self._session.post(
            SELF_ASSERTED_URL,
            params={"tx": trans_id, "p": B2C_POLICY},
            headers={
                **HEADERS_BROWSER,
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
            },
            data={"request_type": "RESPONSE", "signInName": email, "password": password},
        ) as resp:
            testo = await resp.text()
            if resp.status != 200:
                raise SetDistribuzioneAuthError(
                    f"Invio credenziali B2C: HTTP {resp.status}: {testo[:200]!r}"
                )
        try:
            corpo = json.loads(testo)
        except ValueError:
            raise SetDistribuzioneAuthError(
                f"Invio credenziali B2C: risposta non JSON: {testo[:200]!r}"
            ) from None
        stato = str(corpo.get("status")) if isinstance(corpo, dict) else None
        if stato == "200":
            return
        if stato == "400":
            # Risposta standard B2C per email/password errate, con il
            # messaggio da mostrare sotto il form in 'message'.
            raise SetDistribuzioneInvalidCredentials(corpo.get("message") or "credenziali errate")
        raise SetDistribuzioneAuthError(f"Invio credenziali B2C rifiutato: {corpo!r}")

    async def _async_code(self, csrf: str, trans_id: str) -> str:
        async with self._session.get(
            CONFIRMED_URL,
            params={
                "rememberMe": "false",
                "csrf_token": csrf,
                "tx": trans_id,
                "p": B2C_POLICY,
            },
            headers=HEADERS_BROWSER,
            allow_redirects=False,
        ) as resp:
            location = resp.headers.get("Location")
            if resp.status not in (301, 302, 303) or not location:
                testo = await resp.text()
                raise SetDistribuzioneAuthError(
                    f"Conferma login B2C: atteso un redirect, ricevuto HTTP {resp.status}: "
                    f"{testo[:200]!r}"
                )
        parametri = parametri_redirect(location)
        if parametri.get("code"):
            return parametri["code"]
        raise SetDistribuzioneAuthError(
            "Redirect di login senza 'code': "
            f"{parametri.get('error')}: {parametri.get('error_description')}"
        )

    async def _async_scambia_code(self, code: str, verifier: str) -> str:
        dati = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "client_info": "1",
        }
        async with self._session.post(
            TOKEN_URL, data=dati, headers={**HEADERS_BROWSER, **HEADERS_ORIGINE_PORTALE}
        ) as resp:
            try:
                corpo = await resp.json(content_type=None)
            except ValueError:
                testo = await resp.text()
                raise SetDistribuzioneAuthError(
                    f"Token endpoint B2C: HTTP {resp.status}, risposta non JSON: {testo[:200]!r}"
                ) from None
            if resp.status != 200:
                raise SetDistribuzioneAuthError(
                    f"Token endpoint B2C: HTTP {resp.status}: {corpo!r}"
                )
        try:
            return corpo["access_token"]
        except (KeyError, TypeError) as err:
            # Solo le chiavi: il corpo può contenere id_token/refresh_token,
            # che non devono finire nei log.
            chiavi = sorted(corpo) if isinstance(corpo, dict) else type(corpo).__name__
            raise SetDistribuzioneAuthError(
                f"Risposta del token endpoint inattesa (access_token mancante), chiavi: {chiavi}"
            ) from err
