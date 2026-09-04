"""Test per distributors/areti/api.py: costruzione delle chiamate Aura e
parsing delle risposte, nessuna chiamata di rete reale.

Come tests/areti/test_auth.py, api.py non dipende da Home Assistant, ma
viene comunque caricato via importlib bypassando i vari __init__.py della
gerarchia (che lo fanno).

I payload di successo usati come fixture NON sono inventati: sono le
risposte reali ottenute testando l'integrazione il 04/09/2026 (POD reale
con contatore 2G) - anonimizzati sostituendo POD/codice fiscale/codiceBP
con valori di fantasia (stesso formato), la struttura è quella vera.
elementiCurve è troncato a 4 campioni invece di 2976 (31 giorni x 96/
giorno) per brevità del test - la struttura di ogni elemento è reale.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import aiohttp
import pytest

ARETI_DIR = (
    Path(__file__).parent.parent.parent
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "areti"
)


def _load_api_module():
    pkg_name = "areti_test_api"
    if f"{pkg_name}.api" in sys.modules:
        return sys.modules[f"{pkg_name}.api"]

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ARETI_DIR)]
    sys.modules[pkg_name] = pkg

    def _load(modname: str, filename: str):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{modname}", ARETI_DIR / filename
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{modname}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("const", "const.py")
    _load("auth", "auth.py")
    return _load("api", "api.py")


api = _load_api_module()


# ---------------------------------------------------------------------------
# Infrastruttura minima per simulare le risposte Aura (stesso stile di
# tests/edistribuzione/test_api.py) - una sola rotta per test: ogni
# chiamata Aura passa dallo stesso URL (AURA_URL), quindi qui non serve
# instradare per frammento di URL come in edistribuzione, basta la
# prossima risposta configurata.
# ---------------------------------------------------------------------------


class _Risposta:
    def __init__(self, status: int, corpo):
        self.status = status
        self._corpo = corpo

    async def json(self, content_type=None):
        return self._corpo

    def raise_for_status(self):
        if self.status >= 400:
            # request_info con un 'real_url' finto: ClientResponseError.__str__
            # lo legge sempre, e con None crasha nel momento in cui
            # api.py prova a formattare l'errore in un messaggio (str(err)) -
            # non un problema del codice di produzione (una vera risposta
            # aiohttp ha sempre un request_info reale), solo di questo
            # fixture semplificato.
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
    """Ritorna in sequenza le risposte configurate, una per chiamata
    POST - le chiamate Aura di api.py vanno sempre allo stesso URL."""

    def __init__(self, risposte: list[tuple[int, object]]):
        self._risposte = list(risposte)
        self.richieste: list[dict] = []

    def post(self, url, params=None, data=None, headers=None):
        self.richieste.append({"url": url, "params": params, "data": data, "headers": headers})
        status, corpo = self._risposte.pop(0)
        return _Risposta(status, corpo)


def _contesto() -> api.AretiAuraContext:
    return api.AretiAuraContext(fwuid="fw-test", loaded_app_id="app-test", token="token-test")


def _client(risposte: list[tuple[int, object]]) -> tuple[api.AretiApiClient, _Sessione]:
    sessione = _Sessione(risposte)
    return api.AretiApiClient(sessione, _contesto()), sessione


def _involucro_aura(return_value, *, state: str = "SUCCESS", error=None) -> dict:
    """Corpo di risposta Aura genuino: {"actions": [{"state":...,
    "returnValue": {"returnValue": <valore vero>, "cacheable": ...}}]} -
    il doppio 'returnValue' e' come Salesforce incapsula il valore di
    ritorno di ApexActionController.execute, verificato su cattura reale."""
    azione = {"state": state}
    if state == "SUCCESS":
        azione["returnValue"] = {"returnValue": return_value, "cacheable": False}
    else:
        azione["error"] = error or [{"message": "errore generico"}]
    return {"actions": [azione]}


# ---------------------------------------------------------------------------
# async_get_configurations
# ---------------------------------------------------------------------------

# Struttura reale confermata il 04/09/2026, POD/codiceFiscale/codiceBP
# sostituiti con valori di fantasia (stesso formato).
PAYLOAD_CONFIGURATIONS_REALE = {
    "codiceBP": "1200000000",
    "codiceFiscale": "AAABBB00A00A000A",
    "columns": [{"fieldName": "tipo_consumo", "label": "Tipo letture"}],
    "energyOptions": [
        {"label": "EA - Energia attiva entrante", "value": "EA"},
        {"label": "EI - Energia reattiva induttiva", "value": "EI"},
        {"label": "EC - Energia reattiva capacitiva", "value": "EC"},
        {"label": "UA - Energia attiva uscente", "value": "UA"},
        {"label": "UI - Energia induttiva uscente", "value": "UI"},
    ],
    "is2G": True,
    "unitOfMeasureMapping": {
        "EEA": "kWh", "EA": "kWh", "UA": "kWh",
        "EEI": "kVarh", "EI": "kVarh", "EEC": "kVarh", "EC": "kVarh",
        "EUI": "kVarh", "UI": "kVarh",
    },
    "mappingDatiMisura": [
        {"componente": "EA", "fieldName": "F1ActiveEN", "label": "Lettura fascia F1 energia attiva entrante", "order": 1},
    ],
}


class TestAsyncGetConfigurations:
    @pytest.mark.asyncio
    async def test_estrae_codice_bp_e_fiscale_dal_payload_reale(self):
        client, sessione = _client([(200, _involucro_aura(PAYLOAD_CONFIGURATIONS_REALE))])
        config = await client.async_get_configurations("IT001E12345678")
        assert config["codiceBP"] == "1200000000"
        assert config["codiceFiscale"] == "AAABBB00A00A000A"
        assert config["is2G"] is True

    @pytest.mark.asyncio
    async def test_manda_il_pod_nei_parametri(self):
        client, sessione = _client([(200, _involucro_aura(PAYLOAD_CONFIGURATIONS_REALE))])
        await client.async_get_configurations("IT001E12345678")
        message = json.loads(sessione.richieste[0]["data"]["message"])
        params = message["actions"][0]["params"]["params"]
        assert params == {"podName": "IT001E12345678"}

    @pytest.mark.asyncio
    async def test_manda_aura_token_e_context(self):
        client, sessione = _client([(200, _involucro_aura(PAYLOAD_CONFIGURATIONS_REALE))])
        await client.async_get_configurations("IT001E12345678")
        dati = sessione.richieste[0]["data"]
        assert dati["aura.token"] == "token-test"
        aura_context = json.loads(dati["aura.context"])
        assert aura_context["fwuid"] == "fw-test"

    @pytest.mark.asyncio
    async def test_pod_senza_codice_bp_solleva_errore(self):
        """Un POD non associato a questo account (o formato non
        riconosciuto) ritorna un payload senza codiceBP: va trattato come
        errore, non come successo silenzioso con dati mancanti."""
        client, sessione = _client([(200, _involucro_aura({}))])
        with pytest.raises(api.AretiApiError, match="codiceBP"):
            await client.async_get_configurations("IT999X99999999")

    @pytest.mark.asyncio
    async def test_state_error_solleva_eccezione(self):
        client, sessione = _client(
            [(200, _involucro_aura(None, state="ERROR", error=[{"message": "Session expired"}]))]
        )
        with pytest.raises(api.AretiApiError, match="fallita"):
            await client.async_get_configurations("IT001E12345678")

    @pytest.mark.asyncio
    async def test_http_error_solleva_eccezione(self):
        client, sessione = _client([(500, {})])
        with pytest.raises(api.AretiApiError):
            await client.async_get_configurations("IT001E12345678")


# ---------------------------------------------------------------------------
# async_get_misurazioni
# ---------------------------------------------------------------------------

# Struttura reale confermata il 04/09/2026 (agosto 2026, componenteEnergia
# EA), elementiCurve troncato a 4 campioni (il reale ne ha 2976, 31 giorni
# x 96/giorno) - il resto della struttura e' quella vera, inclusa la
# stringa JSON annidata in 'misureByBP'.
_MISURE_BY_BP_REALE = [
    {
        "esitoPosizioneBP": {
            "Tot_Active_EN": "999.000000000000 ",
            "F1ActiveEN": "300.000000000000 ",
            "F2ActiveEN": "300.000000000000 ",
            "F3ActiveEN": "399.000000000000 ",
            "PiccoPotenzaEA": "1.000000 ",
            "DataLettFineM": "20260831",
            "ComponenteEnergia": "EA",
            "Bsnpart": "1200000000",
        },
        "esitoBP": True,
        "bpCode": "1200000000",
        "elementiCurve": [
            {"Value": "0.034", "Ora": "00:00:00", "Data": "2026-08-01"},
            {"Value": "0.017", "Ora": "00:15:00", "Data": "2026-08-01"},
            {"Value": "0.033", "Ora": "00:30:00", "Data": "2026-08-01"},
            {"Value": "0.020", "Ora": "00:45:00", "Data": "2026-08-01"},
        ],
        "elementiAggregati": [
            {"Value": "0.104", "Ora": "23:59:59", "Data": "2026-08-01"},
        ],
    }
]

PAYLOAD_MISURAZIONI_REALE = {
    "bpToName": {"1200000000": "Nome Cognome"},
    "errorMessage": "",
    "esito": True,
    "misureByBP": json.dumps(_MISURE_BY_BP_REALE),
}


class TestAsyncGetMisurazioni:
    @pytest.mark.asyncio
    async def test_estrae_elementi_curve_dal_payload_reale(self):
        client, sessione = _client([(200, _involucro_aura(PAYLOAD_MISURAZIONI_REALE))])
        dettaglio = await client.async_get_misurazioni(
            "IT001E12345678", "1200000000", "AAABBB00A00A000A", "082026"
        )
        assert dettaglio is not None
        assert len(dettaglio["elementiCurve"]) == 4
        assert dettaglio["elementiCurve"][0]["Value"] == "0.034"
        assert dettaglio["esitoPosizioneBP"]["Tot_Active_EN"] == "999.000000000000 "

    @pytest.mark.asyncio
    async def test_manda_inputparamsjson_corretto(self):
        client, sessione = _client([(200, _involucro_aura(PAYLOAD_MISURAZIONI_REALE))])
        await client.async_get_misurazioni(
            "IT001E12345678", "1200000000", "AAABBB00A00A000A", "082026", componente_energia="UA"
        )
        message = json.loads(sessione.richieste[0]["data"]["message"])
        params = message["actions"][0]["params"]["params"]
        input_params = json.loads(params["inputParamsJson"])
        assert input_params == {
            "useMock": False,
            "meseAnno": "082026",
            "codiceBP": "1200000000",
            "codiceFiscale": "AAABBB00A00A000A",
            "pod": "IT001E12345678",
            "componenteEnergia": "UA",
        }

    @pytest.mark.asyncio
    async def test_esito_false_ritorna_none(self):
        """Mese non ancora chiuso/pubblicato: NON e' un errore, e' lo
        stato normale - verificato su dati reali il 04/09/2026 (settembre
        2026, mese in corso, 'esito': true ma 'misureByBP' lista vuota;
        qui esito false per coprire anche quel ramo)."""
        client, sessione = _client(
            [(200, _involucro_aura({"esito": False, "errorMessage": "Nessun dato", "misureByBP": None}))]
        )
        dettaglio = await client.async_get_misurazioni(
            "IT001E12345678", "1200000000", "AAABBB00A00A000A", "092026"
        )
        assert dettaglio is None

    @pytest.mark.asyncio
    async def test_esito_true_ma_lista_vuota_ritorna_none(self):
        """Il caso osservato per davvero il 04/09/2026 per il mese in
        corso: esito true, ma misureByBP e' la stringa JSON di una lista
        vuota - nessun Business Partner, non un errore."""
        client, sessione = _client(
            [(200, _involucro_aura({"esito": True, "errorMessage": "", "misureByBP": "[]"}))]
        )
        dettaglio = await client.async_get_misurazioni(
            "IT001E12345678", "1200000000", "AAABBB00A00A000A", "092026"
        )
        assert dettaglio is None

    @pytest.mark.asyncio
    async def test_state_error_solleva_eccezione(self):
        client, sessione = _client(
            [(200, _involucro_aura(None, state="ERROR", error=[{"message": "Session expired"}]))]
        )
        with pytest.raises(api.AretiApiError):
            await client.async_get_misurazioni(
                "IT001E12345678", "1200000000", "AAABBB00A00A000A", "082026"
            )
