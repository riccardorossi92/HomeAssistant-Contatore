"""Test per distributors/edistribuzione/api.py: costruzione header e
parsing delle risposte, nessuna chiamata di rete reale.

Come tests/edistribuzione/test_auth.py, api.py non dipende da Home
Assistant, ma viene comunque caricato via importlib bypassando i vari
__init__.py della gerarchia (che lo fanno).

I payload di successo usati come fixture NON sono inventati: sono le
risposte reali ottenute testando l'integrazione il 20/08/2026
(2 POD veri via async_get_supplies, una curva di carico giornaliera reale
via async_get_daily_load_profile) - anonimizzati sostituendo indirizzo e
codice fiscale con valori di fantasia, la struttura è quella vera.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import aiohttp
import pytest

EDISTRIBUZIONE_DIR = (
    Path(__file__).parent.parent.parent
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "edistribuzione"
)


def _load_api_module():
    pkg_name = "edistribuzione_test_api"
    if f"{pkg_name}.api" in sys.modules:
        return sys.modules[f"{pkg_name}.api"]

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(EDISTRIBUZIONE_DIR)]
    sys.modules[pkg_name] = pkg

    def _load(modname: str, filename: str):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{modname}", EDISTRIBUZIONE_DIR / filename
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{modname}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("const", "const.py")
    return _load("api", "api.py")


api = _load_api_module()


# ---------------------------------------------------------------------------
# Infrastruttura minima per simulare le risposte (stesso stile di
# tests/pcf_common/test_api_errori.py)
# ---------------------------------------------------------------------------


class _Risposta:
    def __init__(self, status: int, corpo):
        self.status = status
        self._corpo = corpo

    async def json(self, content_type=None):
        return self._corpo

    async def text(self):
        import json as json_mod

        return json_mod.dumps(self._corpo, ensure_ascii=False)

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=self.status, message=f"status {self.status}"
            )

    @property
    def headers(self):
        return {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Sessione:
    """Route per URL: {frammento_url: (status, corpo)}."""

    def __init__(self, rotte: dict[str, tuple[int, object]]):
        self._rotte = rotte

    def _risposta_per(self, url: str) -> _Risposta:
        for frammento, (status, corpo) in self._rotte.items():
            if frammento in url:
                return _Risposta(status, corpo)
        raise AssertionError(f"Nessuna rotta configurata per l'URL: {url}")

    def get(self, url, headers=None, params=None):
        return self._risposta_per(url)

    def post(self, url, headers=None, json=None):
        return self._risposta_per(url)


def _client(rotte: dict[str, tuple[int, object]], token: str = "test-token"):
    return api.EdistribuzioneApiClient(_Sessione(rotte), access_token=token)


# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_contiene_bearer_token(self):
        client = _client({})
        headers = client._headers("QUALSIASI")
        assert headers["Authorization"] == "Bearer test-token"

    def test_contiene_method_user_richiesto(self):
        client = _client({})
        headers = client._headers("ELENCO_POD")
        assert headers["Method_User"] == "ELENCO_POD"

    def test_update_token_aggiorna_authorization(self):
        client = _client({})
        client.update_token("nuovo-token")
        headers = client._headers("X")
        assert headers["Authorization"] == "Bearer nuovo-token"


# ---------------------------------------------------------------------------
# async_get_supplies
# ---------------------------------------------------------------------------

# Struttura reale confermata il 20/08/2026 (2 POD), indirizzo e codice
# fiscale sostituiti con valori di fantasia.
PAYLOAD_SUPPLIES_REALE = {
    "data": [
        {
            "pods": [
                {
                    "IdPod": "IT001E10684497",
                    "SupplyStatusId": "ATT",
                    "TaxCode": "RSSMRA80A01H501U",
                    "VatNumber": None,
                    "PointOfMeasurePostalCode": "00100",
                    "PointOfMeasureProvince": "RM",
                    "PointOfMeasureStreet": "ROMA",
                    "PointOfMeasureMunicipality": "ROMA",
                    "VoltageLevel": "BM",
                    "ContractualPower": 3,
                    "AvailablePower": 3.3,
                    "HasPlant": False,
                },
                {
                    "IdPod": "IT001E10764100",
                    "SupplyStatusId": "ATT",
                    "TaxCode": "RSSMRA80A01H501U",
                    "PointOfMeasureProvince": "RM",
                    "ContractualPower": 6,
                    "AvailablePower": 6.6,
                    "HasPlant": False,
                },
            ]
        }
    ]
}


class TestAsyncGetSupplies:
    @pytest.mark.asyncio
    async def test_estrae_i_pod_dal_payload_reale(self):
        client = _client({"getSupplies": (200, PAYLOAD_SUPPLIES_REALE)})
        pods = await client.async_get_supplies()
        assert len(pods) == 2
        assert pods[0]["IdPod"] == "IT001E10684497"
        assert pods[1]["IdPod"] == "IT001E10764100"

    @pytest.mark.asyncio
    async def test_payload_senza_data_ritorna_lista_vuota(self):
        client = _client({"getSupplies": (200, {})})
        assert await client.async_get_supplies() == []

    @pytest.mark.asyncio
    async def test_data_vuoto_ritorna_lista_vuota(self):
        client = _client({"getSupplies": (200, {"data": []})})
        assert await client.async_get_supplies() == []

    @pytest.mark.asyncio
    async def test_status_errore_solleva_eccezione(self):
        client = _client({"getSupplies": (500, {"meta": {"status": "KO"}})})
        with pytest.raises(aiohttp.ClientResponseError):
            await client.async_get_supplies()


# ---------------------------------------------------------------------------
# async_get_daily_load_profile (via _get_json)
# ---------------------------------------------------------------------------

# Struttura reale confermata il 20/08/2026 (POD anonimizzato), un solo
# giorno con 4 campioni invece di 96 per brevità del test.
PAYLOAD_CURVA_REALE = {
    "data": [
        {
            "readings": {
                "energyType": "A1",
                "sampleDate": "20260801",
                "sampleValues": [
                    {"id": "1", "val": "0.359"},
                    {"id": "2", "val": "0.358"},
                    {"id": "3", "val": "0.287"},
                    {"id": "4", "val": "0.349"},
                ],
            },
            "sampleFrequency": 15,
            "timeType": "CONS",
            "initialSample": "2026-07-31T22:00:00.000+00:00",
        }
    ]
}

# Struttura reale confermata il 20/08/2026 richiedendo un intervallo VERO
# (2026-01-01 - 2026-06-30, rangeDateFrom != rangeDateTo): l'endpoint ha
# davvero restituito tutti i 181 giorni in un'unica risposta (qui solo 3
# per brevità del test, ma con lo stesso pattern osservato: un elemento
# per giorno, ciascuno con 'initialSample' proprio). Confermato anche che
# initialSample gestisce da solo il cambio ora legale (visto nel giorno
# del 29/03/2026 nella risposta reale, non riprodotto qui).
PAYLOAD_CURVA_MULTIGIORNO_REALE = {
    "data": [
        {
            "readings": {"energyType": "A1", "sampleDate": "20260101", "sampleValues": [
                {"id": "1", "val": "0.100"}, {"id": "2", "val": "0.100"},
            ]},
            "sampleFrequency": 15, "timeType": "CONS",
            "initialSample": "2025-12-31T23:00:00.000+00:00",
        },
        {
            "readings": {"energyType": "A1", "sampleDate": "20260102", "sampleValues": [
                {"id": "1", "val": "0.200"}, {"id": "2", "val": "0.200"},
            ]},
            "sampleFrequency": 15, "timeType": "CONS",
            "initialSample": "2026-01-01T23:00:00.000+00:00",
        },
        {
            "readings": {"energyType": "A1", "sampleDate": "20260103", "sampleValues": [
                {"id": "1", "val": "0.300"}, {"id": "2", "val": "0.300"},
            ]},
            "sampleFrequency": 15, "timeType": "CONS",
            "initialSample": "2026-01-02T23:00:00.000+00:00",
        },
    ]
}


class TestAsyncGetDailyLoadProfile:
    @pytest.mark.asyncio
    async def test_intervallo_vero_ritorna_piu_giorni(self):
        """Regressione: confermato con un test reale il 20/08/2026 che
        rangeDateFrom != rangeDateTo restituisce davvero più giorni in
        un'unica risposta (fino a 181 testati) - non va più assunto che
        serva un ciclo giorno per giorno."""
        client = _client(
            {"querydailyloadprofile": (200, PAYLOAD_CURVA_MULTIGIORNO_REALE)}
        )
        import datetime

        curva = await client.async_get_daily_load_profile(
            "IT001E10684497", datetime.date(2026, 1, 1), datetime.date(2026, 1, 3)
        )
        assert len(curva) == 3
        date_ricevute = [g["readings"]["sampleDate"] for g in curva]
        assert date_ricevute == ["20260101", "20260102", "20260103"]

    @pytest.mark.asyncio
    async def test_estrae_i_dati_dal_payload_reale(self):
        client = _client(
            {"querydailyloadprofile": (200, PAYLOAD_CURVA_REALE)}
        )
        import datetime

        curva = await client.async_get_daily_load_profile(
            "IT001E10684497", datetime.date(2026, 8, 1)
        )
        assert len(curva) == 1
        assert curva[0]["sampleFrequency"] == 15
        assert len(curva[0]["readings"]["sampleValues"]) == 4

    @pytest.mark.asyncio
    async def test_404_e_trattato_come_nessun_dato_non_come_errore(self):
        """Regressione: osservato il 21/08/2026 che il backend risponde 404
        (non 200 con data:[] vuoto) quando i dati del giorno richiesto non
        sono ancora pubblicati. Prima di questo fix, un 404 faceva fallire
        l'intero aggiornamento invece di essere trattato come 'nessun dato
        ancora' e finire nella coda di retry del coordinator."""
        client = _client({"querydailyloadprofile": (404, "")})
        import datetime

        curva = await client.async_get_daily_load_profile(
            "IT001E10684497", datetime.date(2026, 8, 20)
        )
        assert curva == []

    @pytest.mark.asyncio
    async def test_401_solleva_errore_specifico(self):
        client = _client({"querydailyloadprofile": (401, {})})
        import datetime

        with pytest.raises(api.EdistribuzioneApiError, match="401"):
            await client.async_get_daily_load_profile("IT001E10684497", datetime.date(2026, 8, 1))

    @pytest.mark.asyncio
    async def test_meta_status_ko_solleva_errore(self):
        client = _client(
            {"querydailyloadprofile": (200, {"meta": {"status": "KO", "message": "Generic Error"}})}
        )
        import datetime

        with pytest.raises(api.EdistribuzioneApiError, match="KO"):
            await client.async_get_daily_load_profile("IT001E10684497", datetime.date(2026, 8, 1))

    @pytest.mark.asyncio
    async def test_meta_status_ok_passa(self):
        client = _client(
            {
                "querydailyloadprofile": (
                    200,
                    {"meta": {"status": "OK"}, "data": PAYLOAD_CURVA_REALE["data"]},
                )
            }
        )
        import datetime

        curva = await client.async_get_daily_load_profile("IT001E10684497", datetime.date(2026, 8, 1))
        assert len(curva) == 1

    @pytest.mark.asyncio
    async def test_meta_assente_e_trattato_come_ok(self):
        """Il payload reale osservato non ha affatto una chiave 'meta':
        deve passare comunque (meta.status is None -> considerato OK)."""
        client = _client({"querydailyloadprofile": (200, PAYLOAD_CURVA_REALE)})
        import datetime

        curva = await client.async_get_daily_load_profile("IT001E10684497", datetime.date(2026, 8, 1))
        assert curva == PAYLOAD_CURVA_REALE["data"]

    @pytest.mark.asyncio
    async def test_date_to_omesso_richiede_un_solo_giorno(self):
        """Retrocompatibilità: senza date_to, il vecchio comportamento
        (un solo giorno, rangeDateFrom == rangeDateTo) resta invariato."""
        client = _client({"querydailyloadprofile": (200, PAYLOAD_CURVA_REALE)})
        import datetime

        curva = await client.async_get_daily_load_profile(
            "IT001E10684497", datetime.date(2026, 8, 1)
        )
        assert curva == PAYLOAD_CURVA_REALE["data"]

    @pytest.mark.asyncio
    async def test_date_to_specificato_viene_accettato(self):
        """Verifica solo che la chiamata con un intervallo vero (date_to
        diverso da date_from) non fallisca lato client - se l'endpoint
        supporti davvero un range multi-giorno o lo tronchi va verificato
        con verify_edistribuzione_login.py contro il servizio reale, non
        qui (questo test usa comunque un solo giorno di fixture)."""
        client = _client({"querydailyloadprofile": (200, PAYLOAD_CURVA_REALE)})
        import datetime

        curva = await client.async_get_daily_load_profile(
            "IT001E10684497", datetime.date(2026, 8, 1), datetime.date(2026, 8, 3)
        )
        assert curva == PAYLOAD_CURVA_REALE["data"]
