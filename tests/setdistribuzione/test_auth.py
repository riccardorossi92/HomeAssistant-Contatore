"""Test per distributors/setdistribuzione/auth.py: login Azure AD B2C con
PKCE, nessuna chiamata di rete reale.

Come tests/ireti/test_auth.py, auth.py non dipende da Home Assistant ma
viene caricato via importlib bypassando i vari __init__.py della gerarchia
(che lo fanno).

Le risposte seguono quelle della cattura reale del 13/10/2026 (con csrf,
transId e code di fantasia): pagina di authorize con il blob SETTINGS,
SelfAsserted {"status": "200"}, redirect 302 con il code nel fragment,
token endpoint con la forma OIDC standard. La risposta a credenziali
errate è quella ricevuta dal vivo il 13/10/2026 con un account inesistente.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import aiohttp
import pytest

SET_DIR = (
    Path(__file__).parent.parent.parent
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "setdistribuzione"
)


def _load_auth_module():
    pkg_name = "setdistribuzione_test_auth"
    if f"{pkg_name}.auth" in sys.modules:
        return sys.modules[f"{pkg_name}.auth"]

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(SET_DIR)]
    sys.modules[pkg_name] = pkg

    def _load(modname: str, filename: str):
        spec = importlib.util.spec_from_file_location(f"{pkg_name}.{modname}", SET_DIR / filename)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{modname}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("const", "const.py")
    return _load("auth", "auth.py")


auth = _load_auth_module()

CSRF = "Y3NyZi1kaS1mYW50YXNpYQ=="
TRANS_ID = "StateProperties=eyJUSUQiOiJmYW50YXNpYSJ9"

# Estratto della pagina reale: il resto dell'HTML non serve al login.
PAGINA_AUTHORIZE = (
    "<html><head><script>var SETTINGS = {\n"
    '  "remoteResource": "https://storagemiddlewareprod001.z6.web.core.windows.net/'
    'cross/signuporsignin-set-ui.html",\n'
    '  "retryLimit": 3,\n'
    '  "api": "CombinedSigninAndSignup",\n'
    f'  "csrf": "{CSRF}",\n'
    f'  "transId": "{TRANS_ID}",\n'
    '  "pageViewId": "26b221a4-40e2-4796-9f1b-1b23fbb41b9e",\n'
    '  "hosts": {"tenant": "/sportelloclientib2cset.onmicrosoft.com/B2C_1A_signin",'
    ' "policy": "B2C_1A_signin"}\n'
    "};</script></head><body></body></html>"
)

LOCATION_CON_CODE = (
    "https://myset.setdistribuzione.it/#state=eyJpZCI6ImZhbnRhc2lhIn0%3d"
    "&client_info=ZmFudGFzaWE&code=code-di-fantasia"
)

TOKEN_REALE = {
    "access_token": "access-di-fantasia",
    "id_token": "id-di-fantasia",
    "token_type": "Bearer",
    "not_before": 1791922929,
    "expires_in": 3600,
    "expires_on": 1791926529,
    "resource": "f7132538-fd2f-4ff9-8f36-5a9f0bb0eee4",
    "client_info": "ZmFudGFzaWE",
    "scope": "https://sportelloclientib2cset.onmicrosoft.com/private/api/read",
    "refresh_token": "refresh-di-fantasia",
    "refresh_token_expires_in": 86400,
}


class _Risposta:
    def __init__(self, status: int, corpo="", headers=None):
        self.status = status
        self._corpo = corpo
        self.headers = headers or {}

    async def text(self):
        return self._corpo if isinstance(self._corpo, str) else repr(self._corpo)

    async def json(self, content_type=None):
        if isinstance(self._corpo, str):
            raise ValueError("risposta non JSON")
        return self._corpo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _CookieJar:
    def __init__(self):
        self.svuotato = False

    def clear(self):
        self.svuotato = True


class _Sessione:
    """Risponde in base al pezzo finale dell'URL; registra ogni chiamata."""

    def __init__(self, risposte: dict[str, _Risposta]):
        self._risposte = risposte
        self.richieste: list[dict] = []
        self.cookie_jar = _CookieJar()

    def _rispondi(self, metodo, url, **kwargs):
        self.richieste.append({"metodo": metodo, "url": url, **kwargs})
        for chiave, risposta in self._risposte.items():
            if url.endswith(chiave):
                return risposta
        raise AssertionError(f"URL inatteso: {url}")

    def get(self, url, **kwargs):
        return self._rispondi("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._rispondi("POST", url, **kwargs)


def _risposte_ok(**sovrascritte):
    risposte = {
        "/oauth2/v2.0/authorize": _Risposta(200, PAGINA_AUTHORIZE),
        "/SelfAsserted": _Risposta(200, '{"status":"200"}'),
        "/api/CombinedSigninAndSignup/confirmed": _Risposta(
            302, "", {"Location": LOCATION_CON_CODE}
        ),
        "/oauth2/v2.0/token": _Risposta(200, TOKEN_REALE),
    }
    risposte.update(sovrascritte)
    return risposte


def _richiesta(sessione: _Sessione, fine_url: str) -> dict:
    return next(r for r in sessione.richieste if r["url"].endswith(fine_url))


class TestLoginRiuscito:
    @pytest.mark.asyncio
    async def test_ritorna_access_token(self):
        sessione = _Sessione(_risposte_ok())
        token = await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "segreto")
        assert token == "access-di-fantasia"

    @pytest.mark.asyncio
    async def test_sequenza_e_cookie_jar_svuotato(self):
        sessione = _Sessione(_risposte_ok())
        await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "segreto")
        assert sessione.cookie_jar.svuotato
        assert [r["url"].rsplit("/", 1)[-1] for r in sessione.richieste] == [
            "authorize", "SelfAsserted", "confirmed", "token",
        ]

    @pytest.mark.asyncio
    async def test_credenziali_inviate_con_csrf_e_transid(self):
        sessione = _Sessione(_risposte_ok())
        await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "segreto")
        invio = _richiesta(sessione, "/SelfAsserted")
        assert invio["params"] == {"tx": TRANS_ID, "p": "B2C_1A_signin"}
        assert invio["headers"]["X-CSRF-TOKEN"] == CSRF
        assert invio["data"] == {
            "request_type": "RESPONSE", "signInName": "a@b.it", "password": "segreto",
        }
        conferma = _richiesta(sessione, "/confirmed")
        assert conferma["params"]["csrf_token"] == CSRF
        assert conferma["allow_redirects"] is False

    @pytest.mark.asyncio
    async def test_pkce_coerente_e_origin_sul_token(self):
        sessione = _Sessione(_risposte_ok())
        await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "segreto")
        challenge = _richiesta(sessione, "/authorize")["params"]["code_challenge"]
        token = _richiesta(sessione, "/token")
        verifier = token["data"]["code_verifier"]
        atteso = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
        assert challenge == atteso.decode()
        assert token["data"]["code"] == "code-di-fantasia"
        assert token["data"]["grant_type"] == "authorization_code"
        # Client SPA: senza Origin B2C rifiuta lo scambio (AADSTS9002327).
        assert token["headers"]["Origin"] == "https://myset.setdistribuzione.it"


class TestLoginFallito:
    @pytest.mark.asyncio
    async def test_credenziali_errate(self):
        sessione = _Sessione(_risposte_ok(**{
            "/SelfAsserted": _Risposta(
                200,
                '{"status":"400","errorCode":"AADB2C90053","message":"Credenziali non valide."}',
            ),
        }))
        with pytest.raises(auth.SetDistribuzioneInvalidCredentials, match="non valide"):
            await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "sbagliata")

    @pytest.mark.asyncio
    async def test_pagina_senza_settings(self):
        sessione = _Sessione(_risposte_ok(**{
            "/oauth2/v2.0/authorize": _Risposta(200, "<html>manutenzione</html>"),
        }))
        with pytest.raises(auth.SetDistribuzioneAuthError, match="SETTINGS"):
            await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "segreto")

    @pytest.mark.asyncio
    async def test_redirect_con_errore_invece_del_code(self):
        sessione = _Sessione(_risposte_ok(**{
            "/api/CombinedSigninAndSignup/confirmed": _Risposta(
                302, "", {"Location": "https://myset.setdistribuzione.it/#error=access_denied"
                                      "&error_description=AADB2C90091"},
            ),
        }))
        with pytest.raises(auth.SetDistribuzioneAuthError, match="access_denied"):
            await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "segreto")

    @pytest.mark.asyncio
    async def test_conferma_senza_redirect(self):
        sessione = _Sessione(_risposte_ok(**{
            "/api/CombinedSigninAndSignup/confirmed": _Risposta(200, "<html></html>"),
        }))
        with pytest.raises(auth.SetDistribuzioneAuthError, match="redirect"):
            await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "segreto")

    @pytest.mark.asyncio
    async def test_token_endpoint_in_errore(self):
        sessione = _Sessione(_risposte_ok(**{
            "/oauth2/v2.0/token": _Risposta(400, {"error": "invalid_request"}),
        }))
        with pytest.raises(auth.SetDistribuzioneAuthError, match="HTTP 400"):
            await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "segreto")

    @pytest.mark.asyncio
    async def test_token_senza_access_token_non_logga_i_token(self):
        sessione = _Sessione(_risposte_ok(**{
            "/oauth2/v2.0/token": _Risposta(200, {"refresh_token": "segreto-da-non-loggare"}),
        }))
        with pytest.raises(auth.SetDistribuzioneAuthError) as exc:
            await auth.SetDistribuzioneAuthClient(sessione).async_login("a@b.it", "segreto")
        assert "segreto-da-non-loggare" not in str(exc.value)

    @pytest.mark.asyncio
    async def test_errore_di_trasporto(self):
        class _SessioneRotta(_Sessione):
            def get(self, url, **kwargs):
                raise aiohttp.ClientConnectionError("connessione rifiutata")

        with pytest.raises(auth.SetDistribuzioneAuthError, match="trasporto"):
            await auth.SetDistribuzioneAuthClient(_SessioneRotta({})).async_login("a@b.it", "x")


def test_parametri_redirect_dal_fragment():
    parametri = auth.parametri_redirect(LOCATION_CON_CODE)
    assert parametri["code"] == "code-di-fantasia"
    assert parametri["state"] == "eyJpZCI6ImZhbnRhc2lhIn0="


@pytest.mark.asyncio
async def test_cookie_jar_non_quota_i_valori():
    """Con i valori tra virgolette B2C risponde 400 "Bad Request" all'invio
    delle credenziali (verificato dal vivo il 13/10/2026)."""
    from yarl import URL

    jar = auth.nuovo_cookie_jar()
    url = URL("https://sportelloclientib2cset.b2clogin.com/")
    jar.update_cookies({"x-ms-cpim-trans": "m1.abc/def+ghi=="}, url)
    assert jar.filter_cookies(url)["x-ms-cpim-trans"].coded_value == "m1.abc/def+ghi=="
