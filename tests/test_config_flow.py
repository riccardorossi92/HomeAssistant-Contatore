"""Test del config flow: wizard ARERA (regione->provincia->comune->lookup),
ramo PCF (credenziali + POD), ramo E-Distribuzione (login/OTP/POD),
options flow e reauth.

Tutto cio' che tocca la rete e' sostituito: async_get_comuni_tree (elenco
ISTAT), async_query_distributore (lookup ARERA), le funzioni di
validazione credenziali/POD del modulo distributore, e i client
EdistribuzioneAuthClient / EdistribuzioneApiClient.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture import config_flow as cf
from custom_components.contatore_letture.arera_lookup import AreraLookupError
from custom_components.contatore_letture.const import (
    CONF_CLIENT_ID,
    CONF_PODS,
    CONF_SECRET_ID,
    DOMAIN,
)
from custom_components.contatore_letture.distributors import duereti, unareti
from custom_components.contatore_letture.distributors.edistribuzione import (
    api as edist_api,
)
from custom_components.contatore_letture.distributors.edistribuzione import (
    auth as edist_auth,
)
from custom_components.contatore_letture.distributors.edistribuzione.auth import (
    EdistribuzioneInvalidCredentials,
    EdistribuzioneInvalidOtp,
    EdistribuzioneParsingError,
)
from custom_components.contatore_letture.distributors.edistribuzione.const import (
    CONF_REFRESH_TOKEN,
)
from custom_components.contatore_letture.distributors.pcf_common.const import (
    CONF_ORA_RICHIESTA,
    CONF_PENDING_TICKET,
)

FAKE_TREE = {
    "Lombardia": {
        "Milano": {
            "Vimodrone": {
                "codice_regione": "03",
                "codice_provincia": "015",
                "codice_comune": "015242",
            },
        },
    },
}

OP_DUERETI = {"ragione_sociale": "DUERETI S.P.A.", "piva": duereti.PIVA}
OP_UNARETI = {"ragione_sociale": "UNARETI S.P.A.", "piva": unareti.PIVA}
OP_SCONOSCIUTO = {"ragione_sociale": "ACME Energia", "piva": "00000000000"}


@pytest.fixture(autouse=True)
def _mock_setup_entry():
    """Evita che una CREATE_ENTRY (o un async_reload dopo reauth/opzioni)
    faccia partire il setup reale della entry - coordinator, sessione
    aiohttp, primo refresh: qui interessa solo l'esito del flow."""
    with patch(
        "custom_components.contatore_letture.async_setup_entry", return_value=True
    ):
        yield


@pytest.fixture
def flow_mocks(monkeypatch):
    """Sostituisce ISTAT + ARERA + validazioni. Default: ARERA trova Duereti,
    credenziali e POD validi (POD con ticket 'TCK')."""
    query = AsyncMock(return_value=[OP_DUERETI])
    monkeypatch.setattr(cf, "async_get_comuni_tree", AsyncMock(return_value=FAKE_TREE))
    monkeypatch.setattr(cf, "async_query_distributore", query)
    monkeypatch.setattr(cf, "pod_gia_configurato", Mock(return_value=None))
    monkeypatch.setattr(duereti, "async_valida_credenziali", AsyncMock(return_value=None))
    monkeypatch.setattr(duereti, "async_valida_pod", AsyncMock(return_value=(None, "TCK")))
    return {"query": query}


async def _fino_a_comune(hass):
    """user -> provincia -> comune (che fa scattare il lookup ARERA)."""
    res = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"regione": "Lombardia"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"provincia": "Milano"}
    )
    return await hass.config_entries.flow.async_configure(
        res["flow_id"], {"comune": "Vimodrone"}
    )


# --- wizard ARERA -----------------------------------------------------------

async def test_un_distributore_supportato_va_a_distributor_info(hass, flow_mocks):
    res = await _fino_a_comune(hass)
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "distributor_info"


async def test_piu_distributori_supportati_chiede_di_scegliere(hass, flow_mocks):
    flow_mocks["query"].return_value = [OP_DUERETI, OP_UNARETI]
    res = await _fino_a_comune(hass)
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "choose_distributor"


async def test_lookup_arera_fallito_va_a_manual_select(hass, flow_mocks):
    flow_mocks["query"].side_effect = AreraLookupError("ARERA irraggiungibile")
    res = await _fino_a_comune(hass)
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "manual_select"


async def test_nessun_operatore_va_a_manual_select(hass, flow_mocks):
    flow_mocks["query"].return_value = []
    res = await _fino_a_comune(hass)
    assert res["step_id"] == "manual_select"


async def test_operatore_non_supportato_va_a_manual_select(hass, flow_mocks):
    flow_mocks["query"].return_value = [OP_SCONOSCIUTO]
    res = await _fino_a_comune(hass)
    assert res["step_id"] == "manual_select"


# --- ramo PCF -------------------------------------------------------------

async def _fino_a_pcf_credentials(hass):
    res = await _fino_a_comune(hass)
    return await hass.config_entries.flow.async_configure(res["flow_id"], {})


async def test_pcf_happy_path_crea_entry_con_ticket(hass, flow_mocks):
    res = await _fino_a_pcf_credentials(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "cid", CONF_SECRET_ID: "sid"}
    )
    assert res["step_id"] == "pcf_add_pod"

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"],
        {"pod": "IT001E00000001", "df": "RSSMRA80A01H501U", "aggiungi_altro": False},
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"]["distributor"] == "duereti"
    assert res["data"][CONF_PODS] == [{"pod": "IT001E00000001", "df": "RSSMRA80A01H501U"}]
    # il ticket di verifica POD viene salvato come pendente
    assert res["data"][CONF_PENDING_TICKET] == "TCK"


async def test_pcf_credenziali_non_valide_mostra_errore(hass, flow_mocks):
    duereti.async_valida_credenziali.return_value = "invalid_auth"
    res = await _fino_a_pcf_credentials(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "x", CONF_SECRET_ID: "y"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "pcf_credentials"
    assert res["errors"] == {"base": "invalid_auth"}


async def test_pcf_pod_duplicato_mostra_errore(hass, flow_mocks):
    cf.pod_gia_configurato.return_value = "IT001E00000001"
    res = await _fino_a_pcf_credentials(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "cid", CONF_SECRET_ID: "sid"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"pod": "IT001E00000001", "df": "DF", "aggiungi_altro": False}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"pod": "pod_duplicato"}


async def test_pcf_aggiungi_altro_pod_poi_crea_entry(hass, flow_mocks):
    res = await _fino_a_pcf_credentials(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "cid", CONF_SECRET_ID: "sid"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"pod": "IT001E00000001", "df": "DF1", "aggiungi_altro": True}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "pcf_add_pod"

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"pod": "IT001E00000002", "df": "DF2", "aggiungi_altro": False}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert [p["pod"] for p in res["data"][CONF_PODS]] == [
        "IT001E00000001",
        "IT001E00000002",
    ]


# --- options flow -------------------------------------------------------------

def _entry_pcf(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "duereti",
            CONF_CLIENT_ID: "cid",
            CONF_SECRET_ID: "sid",
            CONF_PODS: [{"pod": "IT001E00000001", "df": "DF"}],
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_opzioni_menu_pcf(hass):
    entry = _entry_pcf(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == FlowResultType.MENU
    assert set(res["menu_options"]) == {"aggiungi_pod", "rimuovi_pod", "orario"}


async def test_opzioni_orario_salva_valore(hass):
    entry = _entry_pcf(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"next_step_id": "orario"}
    )
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {CONF_ORA_RICHIESTA: 21}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ORA_RICHIESTA] == 21


async def test_opzioni_rimuovi_pod_non_tutti(hass):
    entry = _entry_pcf(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"next_step_id": "rimuovi_pod"}
    )
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"pods_da_rimuovere": ["IT001E00000001"]}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"pods_da_rimuovere": "non_puoi_rimuoverli_tutti"}


# --- reauth PCF -------------------------------------------------------------

async def test_reauth_pcf_aggiorna_credenziali(hass, flow_mocks):
    entry = _entry_pcf(hass)
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert res["step_id"] == "reauth_confirm"

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "nuovo", CONF_SECRET_ID: "nuovo2"}
    )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "reauth_successful"
    assert entry.data[CONF_CLIENT_ID] == "nuovo"


# --- ramo E-Distribuzione (login -> OTP -> POD) ----------------------------

@pytest.fixture
def edist_mocks(monkeypatch):
    """Sostituisce EdistribuzioneAuthClient / EdistribuzioneApiClient e le
    sessioni aiohttp. Default: login e OTP ok, un solo POD sull'account."""
    auth = Mock()
    auth.async_begin_login = AsyncMock(return_value=None)
    auth.async_submit_otp = AsyncMock(
        return_value=SimpleNamespace(access_token="acc", refresh_token="ref-nuovo")
    )
    auth.async_refresh_access_token = AsyncMock(
        return_value=SimpleNamespace(access_token="acc2", refresh_token="ref-nuovo")
    )
    api = Mock()
    api.async_get_supplies = AsyncMock(return_value=[{"IdPod": "IT001E00000009"}])

    monkeypatch.setattr(edist_auth, "EdistribuzioneAuthClient", Mock(return_value=auth))
    monkeypatch.setattr(edist_api, "EdistribuzioneApiClient", Mock(return_value=api))
    monkeypatch.setattr(cf, "async_create_clientsession", lambda *a, **k: Mock())
    monkeypatch.setattr(cf, "async_get_clientsession", lambda *a, **k: Mock())
    return SimpleNamespace(auth=auth, api=api)


async def _fino_a_edist_user(hass):
    """user -> ... -> distributor_info -> submit, con ARERA che dà un
    operatore non supportato e scelta manuale di E-Distribuzione."""
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"regione": "Lombardia"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"provincia": "Milano"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"comune": "Vimodrone"})
    # manual_select -> scelgo edistribuzione
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"distributor": "edistribuzione"}
    )
    # distributor_info -> submit
    return await hass.config_entries.flow.async_configure(res["flow_id"], {})


@pytest.fixture
def _arera_sconosciuto(monkeypatch):
    monkeypatch.setattr(cf, "async_get_comuni_tree", AsyncMock(return_value=FAKE_TREE))
    monkeypatch.setattr(
        cf, "async_query_distributore", AsyncMock(return_value=[OP_SCONOSCIUTO])
    )


async def test_edist_credenziali_non_valide(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.auth.async_begin_login.side_effect = EdistribuzioneInvalidCredentials("no")
    res = await _fino_a_edist_user(hass)
    assert res["step_id"] == "edistribuzione_user"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"base": "invalid_auth"}


async def test_edist_pagina_login_cambiata(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.auth.async_begin_login.side_effect = EdistribuzioneParsingError("markup")
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["errors"] == {"base": "cannot_connect"}


async def test_edist_otp_non_valido(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.auth.async_submit_otp.side_effect = EdistribuzioneInvalidOtp("nope")
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["step_id"] == "edistribuzione_otp"
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "000000"})
    assert res["errors"] == {"base": "invalid_otp"}


async def test_edist_un_solo_pod_crea_entry_subito(hass, _arera_sconosciuto, edist_mocks):
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"]["distributor"] == "edistribuzione"
    assert res["data"][CONF_PODS] == ["IT001E00000009"]
    assert res["data"][CONF_REFRESH_TOKEN] == "ref-nuovo"


async def test_edist_nessun_pod_sull_account_abortisce(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.api.async_get_supplies.return_value = []
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "no_pods_found"


async def test_edist_recupero_pod_fallito_abortisce(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.api.async_get_supplies.side_effect = RuntimeError("boom")
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "edistribuzione_supplies_failed"


async def test_edist_piu_pod_si_scelgono(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.api.async_get_supplies.return_value = [
        {"IdPod": "IT001E00000009"},
        {"IdPod": "IT001E00000010"},
    ]
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["step_id"] == "edistribuzione_pod"

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_PODS: ["IT001E00000010"]}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_PODS] == ["IT001E00000010"]


def _entry_edist(hass, pods=("IT001E00000009",)):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "edistribuzione",
            CONF_PODS: list(pods),
            CONF_REFRESH_TOKEN: "ref-vecchio",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_edist_reauth_aggiorna_refresh_token(hass, edist_mocks):
    entry = _entry_edist(hass)
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert res["step_id"] == "edistribuzione_reauth_user"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "ref-nuovo"


async def test_edist_opzioni_menu(hass):
    entry = _entry_edist(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == FlowResultType.MENU
    assert set(res["menu_options"]) == {
        "edistribuzione_aggiungi_pod",
        "edistribuzione_rimuovi_pod",
        "orario",
    }


async def test_edist_opzioni_aggiungi_pod(hass, edist_mocks):
    entry = _entry_edist(hass, pods=["IT001E00000009"])
    edist_mocks.api.async_get_supplies.return_value = [
        {"IdPod": "IT001E00000009"},
        {"IdPod": "IT001E00000010"},
    ]
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"next_step_id": "edistribuzione_aggiungi_pod"}
    )
    assert res["step_id"] == "edistribuzione_aggiungi_pod"
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"pods_da_aggiungere": ["IT001E00000010"]}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_PODS] == ["IT001E00000009", "IT001E00000010"]
