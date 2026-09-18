"""Test per distributors/ireti/api.py: costruzione delle chiamate REST e
parsing delle risposte, nessuna chiamata di rete reale.

Come tests/ireti/test_auth.py, api.py non dipende da Home Assistant, ma
viene comunque caricato via importlib bypassando i vari __init__.py della
gerarchia (che lo fanno).

Il payload di 'measures-loadprofiles' NON è inventato: è (nella forma,
coi valori mantenuti) quello incollato nell'issue #6 dell'utente
russomichele il 17/09/2026 - la prima e finora unica conferma reale di
questo endpoint. sampleValues è tenuto per intero (96 valori) perché è
proprio la sua lunghezza a essere sotto test (vedi statistics.py per il
perché conta).
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


def _load_api_module():
    pkg_name = "ireti_test_api"
    if f"{pkg_name}.api" in sys.modules:
        return sys.modules[f"{pkg_name}.api"]

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
    return _load("api", "api.py")


api = _load_api_module()


# ---------------------------------------------------------------------------
# Infrastruttura minima per simulare le risposte (stesso stile di
# tests/areti/test_api.py) - una risposta configurata per chiamata, in
# ordine: ogni test dell'API Ireti fa una sola chiamata get/post.
# ---------------------------------------------------------------------------


class _Risposta:
    def __init__(self, status: int, corpo):
        self.status = status
        self._corpo = corpo

    async def json(self, content_type=None):
        return self._corpo

    def raise_for_status(self):
        if self.status >= 400:
            request_info = types.SimpleNamespace(real_url="http://test")
            raise aiohttp.ClientResponseError(
                request_info=request_info, history=(), status=self.status,
                message=f"status {self.status}",
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Sessione:
    """Ritorna in sequenza le risposte configurate, una per chiamata."""

    def __init__(self, risposte: list[tuple[int, object]]):
        self._risposte = list(risposte)
        self.richieste: list[dict] = []

    def get(self, url, headers=None):
        self.richieste.append({"metodo": "GET", "url": url, "headers": headers})
        status, corpo = self._risposte.pop(0)
        return _Risposta(status, corpo)

    def post(self, url, json=None, headers=None):
        self.richieste.append({"metodo": "POST", "url": url, "json": json, "headers": headers})
        status, corpo = self._risposte.pop(0)
        return _Risposta(status, corpo)


def _client(risposte: list[tuple[int, object]]) -> tuple[api.IretiApiClient, _Sessione]:
    sessione = _Sessione(risposte)
    return api.IretiApiClient(sessione, "token-di-fantasia"), sessione


# ---------------------------------------------------------------------------
# async_get_company_id / async_get_consumer / async_get_pods
# ---------------------------------------------------------------------------

# Forma reale (04/09/2026 e 17/09/2026), valori sostituiti con quelli di
# fantasia dove sensibili.
PAYLOAD_COMPANY = {
    "idCompany": "id-company-fantasia",
    "name": "IRETI",
    "description": "Società di distribuzione Energia",
    "host": "smartpod.ireti.it",
}

PAYLOAD_CONSUMER = {
    "responseMessage": "Consumer retituito con successo",
    "reponseCode": 117,
    "entityModel": [
        {
            "idConsumer": "id-consumer-fantasia",
            "codFiscale": "AAABBB00A00A000A",
            "pIVA": None,
            "pods": [],
        }
    ],
}

PAYLOAD_PODS = {
    "responseMessage": "Selezione pod attivi e visibili avvenuta con successo",
    "reponseCode": 326,
    "entityModel": [
        {"idPod": "id-pod-fantasia", "code": "IT020E00000000001", "name": None, "active": True},
    ],
}

PAYLOAD_PODS_VUOTO = {"responseMessage": None, "reponseCode": None, "entityModel": None}


class TestAnagrafica:
    @pytest.mark.asyncio
    async def test_company_id(self):
        client, _ = _client([(200, PAYLOAD_COMPANY)])
        assert await client.async_get_company_id() == "id-company-fantasia"

    @pytest.mark.asyncio
    async def test_company_id_mancante_solleva_errore(self):
        client, _ = _client([(200, {})])
        with pytest.raises(api.IretiApiError, match="idCompany"):
            await client.async_get_company_id()

    @pytest.mark.asyncio
    async def test_consumer_estrae_primo_entity_model(self):
        client, _ = _client([(200, PAYLOAD_CONSUMER)])
        consumer = await client.async_get_consumer("utente")
        assert consumer["idConsumer"] == "id-consumer-fantasia"
        assert consumer["codFiscale"] == "AAABBB00A00A000A"

    @pytest.mark.asyncio
    async def test_consumer_senza_entity_model_solleva_errore(self):
        client, _ = _client([(200, {"entityModel": []})])
        with pytest.raises(api.IretiApiError):
            await client.async_get_consumer("utente")

    @pytest.mark.asyncio
    async def test_pods_ritorna_lista(self):
        client, _ = _client([(200, PAYLOAD_PODS)])
        pods = await client.async_get_pods("id-consumer-fantasia", "id-company-fantasia")
        assert pods == PAYLOAD_PODS["entityModel"]

    @pytest.mark.asyncio
    async def test_pods_vuoto_ritorna_lista_vuota_non_errore(self):
        """Account senza nessun POD associato: lo stato di partenza di
        questa ricerca (issue #6) - non è un errore dell'integrazione."""
        client, _ = _client([(200, PAYLOAD_PODS_VUOTO)])
        pods = await client.async_get_pods("id-consumer-fantasia", "id-company-fantasia")
        assert pods == []


# ---------------------------------------------------------------------------
# async_get_measures_loadprofiles - CONFERMATO su dati reali (issue #6)
# ---------------------------------------------------------------------------

_SAMPLE_VALUES_REALI = [
    0.023, 0.022, 0.018, 0.012, 0.021, 0.023, 0.022, 0.02, 0.013, 0.018,
    0.022, 0.023, 0.022, 0.013, 0.016, 0.022, 0.023, 0.022, 0.015, 0.014,
    0.022, 0.023, 0.022, 0.016, 0.013, 0.022, 0.023, 0.022, 0.018, 0.013,
    0.021, 0.023, 0.023, 0.02, 0.013, 0.02, 0.024, 0.023, 0.02, 0.013,
    0.019, 0.023, 0.023, 0.022, 0.013, 0.018, 0.023, 0.023, 0.023, 0.013,
    0.017, 0.023, 0.024, 0.022, 0.015, 0.015, 0.024, 0.023, 0.023, 0.015,
    0.014, 0.023, 0.023, 0.023, 0.016, 0.013, 0.023, 0.023, 0.022, 0.016,
    0.014, 0.022, 0.023, 0.023, 0.018, 0.012, 0.022, 0.022, 0.022, 0.018,
    0.013, 0.02, 0.023, 0.022, 0.019, 0.013, 0.019, 0.023, 0.022, 0.019,
    0.012, 0.019, 0.023, 0.022, 0.019, 0.013,
]

PAYLOAD_LOADPROFILES_REALE = {
    "responseMessage": None,
    "reponseCode": None,
    "requestId": "285",
    "errorCode": None,
    "pod": "IT020E00000000001",
    "readings": None,
    "intakeReadings": None,
    "loadProfiles": [
        {
            "loadProfileDate": "20/08/2026 00:00:00 +0200",
            "serialNumber": "serial-fantasia",
            "timeType": "",
            "energyType": "A1",
            "measType": "0",
            "sampleValues": _SAMPLE_VALUES_REALI,
        }
    ],
}


class TestAsyncGetMeasuresLoadprofiles:
    @pytest.mark.asyncio
    async def test_estrae_loadprofiles_dal_payload_reale(self):
        client, _ = _client([(200, PAYLOAD_LOADPROFILES_REALE)])
        load_profiles = await client.async_get_measures_loadprofiles(
            "IT020E00000000001", "AAABBB00A00A000A", "orario",
            "2026-07-31T22:00:00.000Z", "2026-08-31T21:59:59.000Z",
        )
        assert len(load_profiles) == 1
        assert len(load_profiles[0]["sampleValues"]) == 96
        assert load_profiles[0]["energyType"] == "A1"

    @pytest.mark.asyncio
    async def test_manda_payload_corretto(self):
        client, sessione = _client([(200, PAYLOAD_LOADPROFILES_REALE)])
        await client.async_get_measures_loadprofiles(
            "IT020E00000000001", "AAABBB00A00A000A", "orario",
            "2026-07-31T22:00:00.000Z", "2026-08-31T21:59:59.000Z",
        )
        corpo = sessione.richieste[0]["json"]
        assert corpo == {
            "operation": "PRELIEVO",
            "podType": "orario",
            "startDate": "2026-07-31T22:00:00.000Z",
            "endDate": "2026-08-31T21:59:59.000Z",
            "customerTaxCodeVat": "AAABBB00A00A000A",
            "pod": "IT020E00000000001",
        }
        assert sessione.richieste[0]["headers"]["Authorization"] == "Bearer token-di-fantasia"

    @pytest.mark.asyncio
    async def test_loadprofiles_assente_ritorna_lista_vuota(self):
        """Range senza ancora nessun dato pubblicato: stato normale, non
        un errore."""
        client, _ = _client([(200, {**PAYLOAD_LOADPROFILES_REALE, "loadProfiles": None})])
        load_profiles = await client.async_get_measures_loadprofiles(
            "IT020E00000000001", "AAABBB00A00A000A", "orario",
            "2026-09-01T00:00:00.000Z", "2026-09-01T23:59:59.000Z",
        )
        assert load_profiles == []

    @pytest.mark.asyncio
    async def test_http_error_solleva_eccezione(self):
        client, _ = _client([(500, {})])
        with pytest.raises(api.IretiApiError):
            await client.async_get_measures_loadprofiles(
                "IT020E00000000001", "AAABBB00A00A000A", "orario",
                "2026-09-01T00:00:00.000Z", "2026-09-01T23:59:59.000Z",
            )


class TestAsyncGetHistory:
    @pytest.mark.asyncio
    async def test_ritorna_corpo_grezzo(self):
        """Endpoint mai confermato con dati reali (vedi api.py): il
        client si limita a chiamarlo e ritornare quello che arriva, è il
        coordinator a decidere cosa fare se 'podType' non c'è."""
        client, _ = _client([(200, {"podType": "orario", "customerPodActive": True})])
        storia = await client.async_get_history(
            "IT020E00000000001", "AAABBB00A00A000A",
            "2026-08-01T00:00:00.000Z", "2026-08-31T23:59:59.000Z",
        )
        assert storia["podType"] == "orario"
