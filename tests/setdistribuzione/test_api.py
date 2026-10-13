"""Test per distributors/setdistribuzione/api.py: path e parametri delle
chiamate e parsing delle risposte, nessuna chiamata di rete reale.

Come test_auth.py, api.py viene caricato via importlib senza Home
Assistant. Le risposte sono quelle reali della cattura del 13/10/2026
(vedi dati_reali.py).
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import date
from pathlib import Path

import aiohttp
import pytest

from .dati_reali import CONSUMI_GIORNO_12_10_2026, PROFILE_CUSTOMER, UTILITY_ACTIVE

SET_DIR = (
    Path(__file__).parent.parent.parent
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "setdistribuzione"
)


def _load_api_module():
    pkg_name = "setdistribuzione_test_api"
    if f"{pkg_name}.api" in sys.modules:
        return sys.modules[f"{pkg_name}.api"]

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
    return _load("api", "api.py")


api = _load_api_module()


class _Risposta:
    def __init__(self, status: int, corpo):
        self.status = status
        self._corpo = corpo

    async def json(self, content_type=None):
        return self._corpo

    async def text(self):
        return repr(self._corpo)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Sessione:
    """Una risposta per chiamata, in ordine; registra URL, parametri e header."""

    def __init__(self, *risposte: _Risposta):
        self._risposte = list(risposte)
        self.richieste: list[dict] = []

    def get(self, url, params=None, headers=None):
        self.richieste.append({"url": url, "params": params, "headers": headers})
        return self._risposte.pop(0)


def _client(*risposte):
    sessione = _Sessione(*risposte)
    return api.SetDistribuzioneApiClient(sessione, "token-fantasia"), sessione


class TestPodAccount:
    @pytest.mark.asyncio
    async def test_pod_dalla_risposta_reale(self):
        client, sessione = _client(_Risposta(200, PROFILE_CUSTOMER), _Risposta(200, UTILITY_ACTIVE))
        pods = await client.async_get_pod_account()
        assert pods == [{
            "pod": "IT999E00000001",
            "fiscal_code": "XXXX",
            "profile": "Retail_SET",
            "contract_id": "0100000001",
            "nome": "Casa",
        }]
        assert sessione.richieste[0]["url"].endswith("/api/secure/profile/customer")
        assert sessione.richieste[1]["url"].endswith("/api/secure/utility/XXXX/active")
        assert sessione.richieste[1]["params"] == {"profile": "Retail_SET"}
        assert sessione.richieste[0]["headers"]["Authorization"] == "Bearer token-fantasia"

    @pytest.mark.asyncio
    async def test_scarta_forniture_non_elettriche(self):
        gas = {"message": "Supplies retrieved", "data": {"utilities": [
            {"podPdr": "00881234567890", "utilityType": "GAS", "contractId": "1"},
        ]}}
        client, _ = _client(_Risposta(200, PROFILE_CUSTOMER), _Risposta(200, gas))
        assert await client.async_get_pod_account() == []

    @pytest.mark.asyncio
    async def test_account_prospect_senza_profili(self):
        """Cattura del 13/09/2026: account registrato senza forniture."""
        client, sessione = _client(_Risposta(200, {"message": "Profile retrieved", "data": []}))
        assert await client.async_get_pod_account() == []
        assert len(sessione.richieste) == 1

    @pytest.mark.asyncio
    async def test_errore_http(self):
        client, _ = _client(_Risposta(401, {"message": "Unauthorized"}))
        with pytest.raises(api.SetDistribuzioneApiError, match="HTTP 401"):
            await client.async_get_pod_account()


class TestConsumiGiorno:
    @pytest.mark.asyncio
    async def test_parametri_della_richiesta(self):
        client, sessione = _client(_Risposta(200, CONSUMI_GIORNO_12_10_2026))
        await client.async_get_consumi_giorno(
            "XXXX", "Retail_SET", "0100000001", "IT999E00000001", date(2026, 10, 12)
        )
        richiesta = sessione.richieste[0]
        assert richiesta["url"].endswith("/api/secure/utility/XXXX/0100000001/consumption")
        # month è 0-based: ottobre = 9, come nella cattura.
        assert richiesta["params"] == {
            "consumptionRange": "F1_F2_F3",
            "consumptionType": "A1",
            "day": 12,
            "month": 9,
            "profile": "Retail_SET",
            "supplyPoint": "IT999E00000001",
            "year": 2026,
        }

    @pytest.mark.asyncio
    async def test_ore_dalla_risposta_reale(self):
        client, _ = _client(_Risposta(200, CONSUMI_GIORNO_12_10_2026))
        ore = await client.async_get_consumi_giorno(
            "XXXX", "Retail_SET", "0100000001", "IT999E00000001", date(2026, 10, 12)
        )
        assert len(ore) == 24
        assert ore[13]["quarters"] == [0.268, 0.457, 0.085, 0.072]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("risposta", [
        _Risposta(404, {"message": "Not found"}),
        _Risposta(200, {"message": "Utility's consumptions retrieved", "data": None}),
        _Risposta(200, {"message": "ok", "data": {"consumptions": []}}),
    ])
    async def test_giorno_non_disponibile_lista_vuota(self, risposta):
        client, _ = _client(risposta)
        assert await client.async_get_consumi_giorno(
            "XXXX", "Retail_SET", "1", "IT999E00000001", date(2026, 10, 13)
        ) == []

    @pytest.mark.asyncio
    async def test_errore_di_trasporto(self):
        class _SessioneRotta:
            def get(self, *args, **kwargs):
                raise aiohttp.ClientConnectionError("connessione rifiutata")

        client = api.SetDistribuzioneApiClient(_SessioneRotta(), "t")
        with pytest.raises(api.SetDistribuzioneApiError, match="trasporto"):
            await client.async_get_consumi_giorno("X", "P", "1", "IT", date(2026, 10, 12))
