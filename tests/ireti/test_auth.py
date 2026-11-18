"""Test per distributors/ireti/auth.py: password grant Keycloak, nessuna
chiamata di rete reale.

Come tests/areti/test_auth.py, auth.py non dipende da Home Assistant, ma
viene comunque caricato via importlib bypassando i vari __init__.py della
gerarchia (che lo fanno).
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import aiohttp
import pytest

IRETI_DIR = (
    Path(__file__).parent.parent.parent
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "ireti"
)


def _load_auth_module():
    pkg_name = "ireti_test_auth"
    if f"{pkg_name}.auth" in sys.modules:
        return sys.modules[f"{pkg_name}.auth"]

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(IRETI_DIR)]
    sys.modules[pkg_name] = pkg

    def _load(modname: str, filename: str):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{modname}", IRETI_DIR / filename
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{modname}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("const", "const.py")
    return _load("auth", "auth.py")


auth = _load_auth_module()


class _Risposta:
    def __init__(self, status: int, corpo):
        self.status = status
        self._corpo = corpo

    async def json(self, content_type=None):
        if isinstance(self._corpo, str):
            # Come aiohttp con orjson: il JSONDecodeError è un ValueError.
            raise ValueError("unexpected character: line 1 column 1 (char 0)")
        return self._corpo

    async def text(self):
        return self._corpo if isinstance(self._corpo, str) else repr(self._corpo)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _CookieJar:
    def __init__(self):
        self.cookie = {"JSESSIONID": "x", "KEYCLOAK_LOCALE": "it"}

    def clear(self):
        self.cookie.clear()


class _Sessione:
    def __init__(self, status: int, corpo):
        self._status = status
        self._corpo = corpo
        self.richieste: list[dict] = []
        self.cookie_jar = _CookieJar()

    def post(self, url, data=None):
        self.richieste.append({"url": url, "data": data, "cookie": dict(self.cookie_jar.cookie)})
        return _Risposta(self._status, self._corpo)


# Risposta reale (campi trimmati) del password grant - verificata il
# 31/08/2026, vedi documentation/protocols/ireti-protocol.md.
TOKEN_REALE = {
    "access_token": "token-di-fantasia",
    "expires_in": 18000,
    "refresh_expires_in": 1800,
    "refresh_token": "refresh-di-fantasia",
    "token_type": "Bearer",
    "scope": "openid profile email",
}


class TestAsyncLogin:
    @pytest.mark.asyncio
    async def test_login_riuscito_ritorna_access_token(self):
        sessione = _Sessione(200, TOKEN_REALE)
        client = auth.IretiAuthClient(sessione)
        token = await client.async_login("utente", "segreto")
        assert token == "token-di-fantasia"

    @pytest.mark.asyncio
    async def test_manda_grant_type_password_e_client_id(self):
        sessione = _Sessione(200, TOKEN_REALE)
        client = auth.IretiAuthClient(sessione)
        await client.async_login("utente", "segreto")
        dati = sessione.richieste[0]["data"]
        assert dati["grant_type"] == "password"
        assert dati["client_id"] == "SmartPOD-Angular"
        assert dati["username"] == "utente"
        assert dati["password"] == "segreto"

    @pytest.mark.asyncio
    async def test_credenziali_invalide_solleva_eccezione_dedicata(self):
        sessione = _Sessione(
            401, {"error": "invalid_grant", "error_description": "Invalid user credentials"}
        )
        client = auth.IretiAuthClient(sessione)
        with pytest.raises(auth.IretiInvalidCredentials):
            await client.async_login("utente", "sbagliata")

    @pytest.mark.asyncio
    async def test_errore_generico_solleva_autherror(self):
        sessione = _Sessione(500, {"error": "server_error"})
        client = auth.IretiAuthClient(sessione)
        with pytest.raises(auth.IretiAuthError):
            await client.async_login("utente", "segreto")

    @pytest.mark.asyncio
    async def test_risposta_senza_access_token_solleva_autherror(self):
        sessione = _Sessione(200, {"token_type": "Bearer"})
        client = auth.IretiAuthClient(sessione)
        with pytest.raises(auth.IretiAuthError, match="access_token"):
            await client.async_login("utente", "segreto")

    @pytest.mark.asyncio
    async def test_errore_di_trasporto_solleva_autherror(self):
        class _SessioneRotta:
            cookie_jar = _CookieJar()

            def post(self, url, data=None):
                raise aiohttp.ClientConnectionError("connessione rifiutata")

        client = auth.IretiAuthClient(_SessioneRotta())
        with pytest.raises(auth.IretiAuthError):
            await client.async_login("utente", "segreto")

    @pytest.mark.asyncio
    async def test_login_parte_senza_cookie(self):
        """Issue #10: con JSESSIONID/KEYCLOAK_LOCALE nella sessione il WAF
        risponde 403 al secondo login: il POST deve partire senza cookie."""
        sessione = _Sessione(200, TOKEN_REALE)
        client = auth.IretiAuthClient(sessione)
        await client.async_login("utente", "segreto")
        assert sessione.richieste[0]["cookie"] == {}

    @pytest.mark.asyncio
    async def test_pagina_html_non_403_solleva_autherror(self):
        """Una risposta non JSON diversa da 403 resta un IretiAuthError
        leggibile (issue #10), senza ripiegare sul flusso a form."""
        sessione = _Sessione(502, "<html>Bad Gateway</html>")
        client = auth.IretiAuthClient(sessione)
        with pytest.raises(auth.IretiAuthError, match="HTTP 502, risposta non JSON"):
            await client.async_login("utente", "segreto")
        assert len(sessione.richieste) == 1


PAGINA_403 = "<html>403 Response Code - Request ID: abc</html>"
PAGINA_LOGIN = (
    '<html><form id="kc-form-login" class="form" onsubmit="return true;" '
    'action="https://smartpod.ireti.it/auth/realms/IRENSmartPOD/login-actions/'
    'authenticate?session_code=abc&amp;execution=e1&amp;client_id=SmartPOD-Angular" '
    'method="post"><input name="username"><input name="password"></form></html>'
)


class _RispostaGenerica:
    def __init__(self, status, corpo="", headers=None, url="https://smartpod.ireti.it/x"):
        self.status = status
        self._corpo = corpo
        self.headers = headers or {}
        self.url = url

    async def json(self, content_type=None):
        if isinstance(self._corpo, str):
            raise ValueError("non json")
        return self._corpo

    async def text(self):
        return self._corpo if isinstance(self._corpo, str) else repr(self._corpo)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _SessioneForm:
    """Simula il portale con il WAF che blocca il password grant (403) e
    Keycloak che serve il form e poi il code."""

    def __init__(self, esito_form="code", grant_password=(403, PAGINA_403)):
        self.esito_form = esito_form
        self.grant_password = grant_password
        self.richieste: list[tuple[str, str, dict]] = []
        self.cookie_jar = _CookieJar()
        self.state = None
        self.challenge = None

    def get(self, url, params=None, headers=None):
        self.richieste.append(("GET", url, {"params": params, "cookie": dict(self.cookie_jar.cookie)}))
        self.state = params["state"]
        self.challenge = params["code_challenge"]
        self.cookie_jar.cookie["AUTH_SESSION_ID"] = "kc"
        return _RispostaGenerica(200, PAGINA_LOGIN)

    def post(self, url, data=None, headers=None, allow_redirects=True):
        self.richieste.append(("POST", url, {"data": data, "cookie": dict(self.cookie_jar.cookie)}))
        if url.endswith("/token"):
            if data["grant_type"] == "password":
                return _RispostaGenerica(*self.grant_password)
            return _RispostaGenerica(200, TOKEN_REALE)
        # POST del form di login
        if self.esito_form == "code":
            return _RispostaGenerica(
                302,
                headers={
                    "Location": f"https://smartpod.ireti.it/?state={self.state}"
                    "&session_state=s&code=codice-di-fantasia"
                },
            )
        if self.esito_form == "credenziali":
            return _RispostaGenerica(200, PAGINA_LOGIN)
        if self.esito_form == "state":
            return _RispostaGenerica(
                302, headers={"Location": "https://smartpod.ireti.it/?state=altro&code=c"}
            )
        return _RispostaGenerica(403, PAGINA_403)


class TestFallbackFormIssue21:
    """Issue #21: il WAF risponde 403 al password grant con password che
    contengono caratteri come '<' o '/*'; il form del browser non e' filtrato."""

    @pytest.mark.asyncio
    async def test_403_del_waf_ripiega_sul_flusso_a_form(self):
        sessione = _SessioneForm()
        token = await auth.IretiAuthClient(sessione).async_login("utente", "pa<ss>")
        assert token == "token-di-fantasia"
        metodi = [(m, u.rsplit("/", 1)[-1].split("?")[0]) for m, u, _ in sessione.richieste]
        assert metodi == [
            ("POST", "token"),
            ("GET", "auth"),
            ("POST", "authenticate"),
            ("POST", "token"),
        ]

    @pytest.mark.asyncio
    async def test_la_password_va_solo_nel_form_e_il_code_senza_password(self):
        sessione = _SessioneForm()
        await auth.IretiAuthClient(sessione).async_login("utente", "pa<ss>")
        _, url_form, extra = sessione.richieste[2]
        assert url_form.startswith("https://smartpod.ireti.it/")
        assert "&amp;" not in url_form  # l'action e' stata de-escapata
        assert extra["data"] == {"username": "utente", "password": "pa<ss>"}
        scambio = sessione.richieste[3][2]["data"]
        assert scambio["grant_type"] == "authorization_code"
        assert scambio["code"] == "codice-di-fantasia"
        assert "password" not in scambio
        assert scambio["code_verifier"]

    @pytest.mark.asyncio
    async def test_pkce_challenge_e_sha256_del_verifier(self):
        import base64
        import hashlib

        sessione = _SessioneForm()
        await auth.IretiAuthClient(sessione).async_login("utente", "pa<ss>")
        verifier = sessione.richieste[3][2]["data"]["code_verifier"]
        atteso = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert sessione.challenge == atteso
        assert 43 <= len(verifier) <= 128

    @pytest.mark.asyncio
    async def test_scambio_del_code_parte_senza_cookie(self):
        """Il WAF rifiuta il token endpoint con i cookie di Keycloak."""
        sessione = _SessioneForm()
        await auth.IretiAuthClient(sessione).async_login("utente", "pa<ss>")
        assert sessione.richieste[3][2]["cookie"] == {}
        assert sessione.cookie_jar.cookie == {}

    @pytest.mark.asyncio
    async def test_credenziali_errate_nel_form(self):
        sessione = _SessioneForm(esito_form="credenziali")
        with pytest.raises(auth.IretiInvalidCredentials):
            await auth.IretiAuthClient(sessione).async_login("utente", "pa<ss>")

    @pytest.mark.asyncio
    async def test_state_diverso_solleva_autherror(self):
        sessione = _SessioneForm(esito_form="state")
        with pytest.raises(auth.IretiAuthError, match="state"):
            await auth.IretiAuthClient(sessione).async_login("utente", "pa<ss>")

    @pytest.mark.asyncio
    async def test_waf_anche_sul_form_solleva_autherror(self):
        sessione = _SessioneForm(esito_form="waf")
        with pytest.raises(auth.IretiAuthError, match="HTTP 403") as exc:
            await auth.IretiAuthClient(sessione).async_login("utente", "pa<ss>")
        assert not isinstance(exc.value, auth.IretiInvalidCredentials)

    @pytest.mark.asyncio
    async def test_password_normale_non_tocca_il_form(self):
        sessione = _SessioneForm(grant_password=(200, TOKEN_REALE))
        await auth.IretiAuthClient(sessione).async_login("utente", "segreto")
        assert len(sessione.richieste) == 1

    def test_action_fuori_dal_portale_non_riceve_le_credenziali(self):
        pagina = PAGINA_LOGIN.replace("https://smartpod.ireti.it", "https://evil.example")
        with pytest.raises(auth.IretiHostNonAmmesso, match="evil.example"):
            auth.IretiAuthClient._trova_action_login(pagina)

    @pytest.mark.asyncio
    async def test_action_fuori_dal_portale_nessun_post_delle_credenziali(self):
        class _SessioneFormAltrove(_SessioneForm):
            def get(self, url, params=None, headers=None):
                super().get(url, params=params, headers=headers)
                return _RispostaGenerica(
                    200, PAGINA_LOGIN.replace("https://smartpod.ireti.it", "https://evil.example")
                )

        sessione = _SessioneFormAltrove()
        with pytest.raises(auth.IretiHostNonAmmesso):
            await auth.IretiAuthClient(sessione).async_login("utente", "a<b")
        # Il password grant (bloccato dal WAF) e la pagina, nessun POST al form.
        assert [(m, u.rsplit("/", 1)[-1]) for m, u, _ in sessione.richieste] == [
            ("POST", "token"),
            ("GET", "auth"),
        ]

    def test_form_senza_action_di_login_resta_non_trovato(self):
        with pytest.raises(auth.IretiAuthError, match="Form di login Ireti non trovato"):
            auth.IretiAuthClient._trova_action_login("<html><form action=\"/altro\"></form>")
