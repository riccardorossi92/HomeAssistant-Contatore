"""Test di distributors/edyna/auth.py: login a postback sul portale Terranova
PUF, nessuna chiamata di rete reale. La sessione è finta e riproduce, nell'ordine,
le quattro richieste del login (vedi docstring del modulo)."""
from __future__ import annotations

import aiohttp
import pytest

from custom_components.contatore_letture.distributors.edyna import auth as modulo
from custom_components.contatore_letture.distributors.edyna.const import (
    CAMPO_PASSWORD,
    CAMPO_PULSANTE_LOGIN,
    CAMPO_UTENTE,
    SCRIPT_MANAGER,
    SECOND_POSTBACK,
)

from . import dati_reali as d


class _Risposta:
    def __init__(self, testo: str, status: int = 200):
        self.status = status
        self._testo = testo

    async def text(self):
        return self._testo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Jar:
    def __init__(self):
        self.svuotato = 0

    def clear(self):
        self.svuotato += 1


class SessioneFinta:
    """Risponde in ordine; registra metodo, URL, intestazioni e corpo."""

    def __init__(self, *risposte):
        self._risposte = list(risposte)
        self.chiamate: list[dict] = []
        self.cookie_jar = _Jar()

    def _chiama(self, metodo, url, **kw):
        self.chiamate.append({"metodo": metodo, "url": url, **kw})
        r = self._risposte.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, **kw):
        return self._chiama("GET", url, **kw)

    def post(self, url, **kw):
        return self._chiama("POST", url, **kw)


def _login_riuscito():
    return SessioneFinta(
        _Risposta(d.MODULO_LOGIN),
        _Risposta(d.RISPOSTA_LOGIN_OK),
        _Risposta(d.pagina_home(1)),
        _Risposta(d.pagina_home(2)),
    )


async def test_login_riuscito_percorre_i_quattro_passi():
    sessione = _login_riuscito()
    pagina = await modulo.EdynaAuthClient(sessione).async_login("mario", "segreta")

    assert [(c["metodo"], c["url"].split("?")[0].rsplit("/", 1)[-1]) for c in sessione.chiamate] == [
        ("GET", "Login.tws"),
        ("POST", "Login.tws"),
        ("GET", "Single.tws"),
        ("POST", "Single.tws"),
    ]
    assert sessione.cookie_jar.svuotato == 1
    # Torna la pagina dopo il secondo postback (viewstate n. 2).
    assert pagina.find("input", attrs={"name": "__VIEWSTATE"})["value"] == "VS-2"


async def test_post_di_login_come_il_browser():
    sessione = _login_riuscito()
    await modulo.EdynaAuthClient(sessione).async_login("mario", "segreta")

    post = sessione.chiamate[1]
    campi = dict(post["data"])
    assert campi[SCRIPT_MANAGER].endswith("|ctl00$ctl00$body$body$cLogin$btnLogin")
    assert campi[CAMPO_UTENTE] == "mario" and campi[CAMPO_PASSWORD] == "segreta"
    assert campi[CAMPO_PULSANTE_LOGIN] == "Accedi" and campi["__ASYNCPOST"] == "true"
    # Viewstate letto dal modulo, __EVENTTARGET vuoto.
    assert campi["__VIEWSTATE"] == "VS-LOGIN" and campi["__EVENTTARGET"] == ""
    assert post["headers"]["X-MicrosoftAjax"] == "Delta=true"
    assert post["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert post["headers"]["Origin"] == "https://portaledistributore.edyna.net"


async def test_secondo_postback():
    sessione = _login_riuscito()
    await modulo.EdynaAuthClient(sessione).async_login("mario", "segreta")

    campi = dict(sessione.chiamate[3]["data"])
    assert campi["__EVENTTARGET"] == SECOND_POSTBACK
    assert campi["__VIEWSTATE"] == "VS-1"


async def test_credenziali_rifiutate():
    sessione = SessioneFinta(_Risposta(d.MODULO_LOGIN), _Risposta(d.RISPOSTA_LOGIN_RIFIUTATA))
    with pytest.raises(modulo.EdynaInvalidCredentials):
        await modulo.EdynaAuthClient(sessione).async_login("mario@esempio.it", "x")
    # Si ferma al POST: niente richieste successive.
    assert len(sessione.chiamate) == 2


async def test_risposta_senza_redirect_ne_errore_e_errore_generico():
    sessione = SessioneFinta(_Risposta(d.MODULO_LOGIN), _Risposta("1|#||4|5|updatePanel|x|abc|"))
    with pytest.raises(modulo.EdynaAuthError) as errore:
        await modulo.EdynaAuthClient(sessione).async_login("mario", "x")
    assert not isinstance(errore.value, modulo.EdynaInvalidCredentials)


async def test_http_inatteso_sul_modulo():
    sessione = SessioneFinta(_Risposta("", status=503))
    with pytest.raises(modulo.EdynaAuthError, match="503"):
        await modulo.EdynaAuthClient(sessione).async_login("mario", "x")


async def test_errore_di_trasporto():
    sessione = SessioneFinta(aiohttp.ClientConnectionError("rete giù"))
    with pytest.raises(modulo.EdynaAuthError, match="trasporto"):
        await modulo.EdynaAuthClient(sessione).async_login("mario", "x")


async def test_dopo_il_login_ancora_il_modulo():
    sessione = SessioneFinta(
        _Risposta(d.MODULO_LOGIN), _Risposta(d.RISPOSTA_LOGIN_OK), _Risposta(d.MODULO_LOGIN)
    )
    with pytest.raises(modulo.EdynaAuthError, match="modulo di accesso"):
        await modulo.EdynaAuthClient(sessione).async_login("mario", "x")
