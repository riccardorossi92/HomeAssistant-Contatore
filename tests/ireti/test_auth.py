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
        return self._corpo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Sessione:
    def __init__(self, status: int, corpo):
        self._status = status
        self._corpo = corpo
        self.richieste: list[dict] = []

    def post(self, url, data=None):
        self.richieste.append({"url": url, "data": data})
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
            def post(self, url, data=None):
                raise aiohttp.ClientConnectionError("connessione rifiutata")

        client = auth.IretiAuthClient(_SessioneRotta())
        with pytest.raises(auth.IretiAuthError):
            await client.async_login("utente", "segreto")
