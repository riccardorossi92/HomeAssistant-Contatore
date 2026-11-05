"""Login Edyna: sessione a cookie sul portale Terranova PUF, nessun OTP.

Sequenza verificata su cattura reale (05/11/2026, account di prova senza
POD; vedi documentation/protocols/edyna-protocol.md, sezione "Login"),
identica a V-Reti e DEA:

    1. GET  Login.tws?ReturnUrl=...Single.tws  -> modulo con viewstate
    2. POST Login.tws?ReturnUrl=... come UpdatePanel ASP.NET
       (X-MicrosoftAjax: Delta=true) con nome utente (NON l'email) e password
       -> risposta 'pageRedirect' + Set-Cookie di sessione (nome a forma di
       GUID, HttpOnly). Credenziali errate: 200 senza redirect né cookie.
    3. GET  Single.tws con il cookie
    4. POST Single.tws con __EVENTTARGET=body_ctl00_SecondPostback ->
       menu e home

Il passo 1 non è nelle catture (la pagina era già caricata): è la lettura
del modulo da cui il browser prende viewstate ed event validation.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup

from .const import (
    CAMPO_PASSWORD,
    CAMPO_PULSANTE_LOGIN,
    CAMPO_UTENTE,
    HEADERS_BROWSER,
    LOGIN_URL,
    PORTALE_URL,
    SCRIPT_MANAGER,
    SCRIPT_MANAGER_LOGIN,
    SECOND_POSTBACK,
    SINGLE_PATH,
    SINGLE_URL,
)
from .parse import (
    EdynaParseError,
    campi_form,
    dati_postback,
    e_pagina_di_login,
    parse_html,
    redirect_dal_delta,
)

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=60)
_LOGIN_CON_RITORNO = f"{LOGIN_URL}?ReturnUrl={quote(SINGLE_PATH, safe='')}"
_MESSAGGIO_CREDENZIALI = "Credenziali di accesso non valide"


class EdynaAuthError(Exception):
    """Errore generico di autenticazione."""


class EdynaInvalidCredentials(EdynaAuthError):
    """Nome utente o password rifiutati dal portale."""


def nuovo_cookie_jar() -> aiohttp.CookieJar:
    return aiohttp.CookieJar()


class EdynaAuthClient:
    """Esegue il login e ritorna la pagina iniziale (già col menu).

    La sessione deve avere un cookie jar proprio (nuovo_cookie_jar): il
    cookie di sessione del portale vive lì. Viene svuotato a ogni login."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_login(self, username: str, password: str) -> BeautifulSoup:
        """Solleva EdynaInvalidCredentials su credenziali errate,
        EdynaAuthError per qualunque altro problema (rete, risposta
        inattesa)."""
        self._session.cookie_jar.clear()
        try:
            return await self._login(username, password)
        except aiohttp.ClientError as err:
            raise EdynaAuthError(f"Errore di trasporto durante il login Edyna: {err}") from err
        except TimeoutError as err:
            raise EdynaAuthError("Timeout durante il login Edyna") from err
        except EdynaParseError as err:
            raise EdynaAuthError(f"Pagina di login Edyna inattesa: {err}") from err

    async def _login(self, username: str, password: str) -> BeautifulSoup:
        loop = asyncio.get_running_loop()

        # 1. Modulo di login (viewstate, event validation, __PREVIOUSPAGE...)
        async with self._session.get(
            _LOGIN_CON_RITORNO, headers=HEADERS_BROWSER, timeout=_TIMEOUT
        ) as resp:
            if resp.status != 200:
                raise EdynaAuthError(f"GET Login.tws: HTTP {resp.status}")
            testo = await resp.text()
        campi = await loop.run_in_executor(None, lambda: campi_form(parse_html(testo)))

        # 2. Login come UpdatePanel. Come nel browser: il pulsante premuto e
        # __ASYNCPOST viaggiano nel corpo, __EVENTTARGET resta vuoto.
        dati = dati_postback(
            campi,
            "",
            sovrascrivi={CAMPO_UTENTE: username, CAMPO_PASSWORD: password},
        )
        dati = [(SCRIPT_MANAGER, SCRIPT_MANAGER_LOGIN), *dati]
        dati += [("__ASYNCPOST", "true"), (CAMPO_PULSANTE_LOGIN, "Accedi")]
        headers = {
            **HEADERS_BROWSER,
            "Origin": PORTALE_URL,
            "Referer": _LOGIN_CON_RITORNO,
            "X-MicrosoftAjax": "Delta=true",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        async with self._session.post(
            _LOGIN_CON_RITORNO, data=dati, headers=headers, timeout=_TIMEOUT
        ) as resp:
            if resp.status != 200:
                raise EdynaAuthError(f"POST Login.tws: HTTP {resp.status}")
            risposta = await resp.text()
        if redirect_dal_delta(risposta) is None:
            if _MESSAGGIO_CREDENZIALI in risposta:
                raise EdynaInvalidCredentials("Nome utente o password non validi")
            raise EdynaAuthError("Login Edyna: risposta senza redirect né errore di credenziali")

        # 3. Pagina iniziale
        async with self._session.get(
            SINGLE_URL, headers={**HEADERS_BROWSER, "Referer": _LOGIN_CON_RITORNO}, timeout=_TIMEOUT
        ) as resp:
            if resp.status != 200:
                raise EdynaAuthError(f"GET Single.tws: HTTP {resp.status}")
            testo = await resp.text()
        soup = await loop.run_in_executor(None, parse_html, testo)
        if e_pagina_di_login(soup):
            raise EdynaAuthError("Dopo il login il portale mostra ancora il modulo di accesso")

        # 4. Secondo postback automatico del browser: porta il menu completo.
        dati = dati_postback(await loop.run_in_executor(None, campi_form, soup), SECOND_POSTBACK)
        async with self._session.post(
            SINGLE_URL,
            data=dati,
            headers={**HEADERS_BROWSER, "Origin": PORTALE_URL, "Referer": SINGLE_URL},
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise EdynaAuthError(f"POST Single.tws (SecondPostback): HTTP {resp.status}")
            testo = await resp.text()
        soup = await loop.run_in_executor(None, parse_html, testo)
        if e_pagina_di_login(soup):
            raise EdynaAuthError("Sessione rifiutata dal portale dopo il login")
        return soup
