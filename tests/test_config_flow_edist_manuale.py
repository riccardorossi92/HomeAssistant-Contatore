"""Config flow e opzioni della modalita' "caricamento manuale" di
E-Distribuzione: niente login, solo il POD, e i CSV del portale caricati
dalle opzioni (issue #11)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture import config_flow as cf
from custom_components.contatore_letture.const import CONF_PODS, DOMAIN
from custom_components.contatore_letture.distributors.edistribuzione import (
    const as edist_const,
)
from custom_components.contatore_letture.distributors.edistribuzione import (
    manuale as edist_manuale,
)
from custom_components.contatore_letture.distributors.edistribuzione.manuale import (
    EdistribuzioneManuale,
)

from .test_config_flow import FAKE_TREE, OP_SCONOSCIUTO

POD = "IT001E12345678"
FILE_ID = "6f1c2d3e-4a5b-4c6d-8e7f-90a1b2c3d4e5"

COLONNE = [
    f"{(i * 15) // 60:02d}:{(i * 15) % 60:02d}-{((i + 1) * 15) // 60 % 24:02d}:{((i + 1) * 15) % 60:02d}"
    for i in range(96)
]
CSV_DUE_GIORNI = (
    "Giorno;" + ";".join(COLONNE) + "\n"
    + '"01/08/2026";' + ";".join(['"0,1"'] * 96) + ";\n"
    + '"02/08/2026";' + ";".join(['"0,2"'] * 96) + ";\n"
).encode()


@pytest.fixture(autouse=True)
def _arera_sconosciuto(monkeypatch):
    monkeypatch.setattr(cf, "async_get_comuni_tree", AsyncMock(return_value=FAKE_TREE))
    monkeypatch.setattr(
        cf, "async_query_distributore", AsyncMock(return_value=[OP_SCONOSCIUTO])
    )


async def _fino_a_modalita(hass):
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"regione": "Lombardia"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"provincia": "Milano"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"comune": "Vimodrone"})
    return await hass.config_entries.flow.async_configure(
        res["flow_id"], {"distributor": "edistribuzione"}
    )


async def test_e_distribuzione_chiede_la_modalita(hass):
    res = await _fino_a_modalita(hass)
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "edistribuzione_modalita"


async def test_automatico_non_selezionabile_finche_disattivato(hass, monkeypatch):
    monkeypatch.setattr(edist_const, "SCARICAMENTO_AUTOMATICO_DISPONIBILE", False)
    res = await _fino_a_modalita(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"modalita": "automatica"}
    )
    assert res["step_id"] == "edistribuzione_modalita"
    assert res["errors"] == {"modalita": "automatico_non_disponibile"}


async def test_automatico_riattivato_porta_al_login(hass, monkeypatch):
    monkeypatch.setattr(edist_const, "SCARICAMENTO_AUTOMATICO_DISPONIBILE", True)
    res = await _fino_a_modalita(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"modalita": "automatica"}
    )
    assert res["step_id"] == "edistribuzione_user"


async def test_manuale_crea_la_voce_col_solo_pod(hass):
    res = await _fino_a_modalita(hass)
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"modalita": "manuale"})
    assert res["step_id"] == "edistribuzione_manuale_pod"

    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"pod": "IT001E123"})
    assert res["errors"] == {"pod": "pod_formato_non_valido"}

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"pod": " it001e12345678 "}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"] == {
        "distributor": "edistribuzione",
        "comune": "Vimodrone",
        "modalita": "manuale",
        CONF_PODS: [POD],
    }
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert isinstance(hass.data[DOMAIN][entry.entry_id], EdistribuzioneManuale)


async def test_manuale_pod_gia_configurato(hass):
    MockConfigEntry(
        domain=DOMAIN, title="Altra", data={"distributor": "edistribuzione", CONF_PODS: [POD]}
    ).add_to_hass(hass)
    res = await _fino_a_modalita(hass)
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"modalita": "manuale"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"pod": POD})
    assert res["errors"] == {"pod": "pod_duplicato"}


# --- opzioni: caricamento del CSV -------------------------------------------


@pytest.fixture
async def entry_manuale(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"E-Distribuzione {POD} (caricamento manuale)",
        data={"distributor": "edistribuzione", "modalita": "manuale", CONF_PODS: [POD]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def import_ore(monkeypatch):
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(edist_manuale, "async_import_ore", mock)
    return mock


async def test_opzioni_vanno_dritte_al_caricamento(hass, entry_manuale):
    res = await hass.config_entries.options.async_init(entry_manuale.entry_id)
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "edistribuzione_importa_file"


async def test_csv_importato_sul_pod_della_voce(hass, entry_manuale, import_ore, monkeypatch):
    monkeypatch.setattr(cf, "_leggi_file_caricato", lambda hass, file_id: CSV_DUE_GIORNI)
    res = await hass.config_entries.options.async_init(entry_manuale.entry_id)
    res = await hass.config_entries.options.async_configure(res["flow_id"], {"file": FILE_ID})

    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "csv_importato"
    assert res["description_placeholders"] == {
        "giorni": "2",
        "data_da": "01/08/2026",
        "data_a": "02/08/2026",
        "kwh": "28,800",
        "pod": POD,
    }
    import_ore.assert_awaited_once()
    _, pod, ore = import_ore.await_args.args
    assert pod == POD
    assert len(ore) == 48


async def test_csv_non_valido_resta_sul_form(hass, entry_manuale, import_ore, monkeypatch):
    monkeypatch.setattr(cf, "_leggi_file_caricato", lambda hass, file_id: b"Data;Valore\n")
    res = await hass.config_entries.options.async_init(entry_manuale.entry_id)
    res = await hass.config_entries.options.async_configure(res["flow_id"], {"file": FILE_ID})

    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"file": "csv_non_valido"}
    assert "intestazione" in res["description_placeholders"]["dettaglio"]
    import_ore.assert_not_awaited()


async def test_file_caricato_non_piu_disponibile(hass, entry_manuale, import_ore, monkeypatch):
    def _scaduto(hass, file_id):
        raise ValueError("File does not exist")

    monkeypatch.setattr(cf, "_leggi_file_caricato", _scaduto)
    res = await hass.config_entries.options.async_init(entry_manuale.entry_id)
    res = await hass.config_entries.options.async_configure(res["flow_id"], {"file": FILE_ID})
    assert res["errors"] == {"file": "file_non_disponibile"}


async def test_voce_manuale_si_scarica_senza_piattaforme(hass, entry_manuale):
    assert await hass.config_entries.async_unload(entry_manuale.entry_id)
    assert entry_manuale.entry_id not in hass.data[DOMAIN]


async def test_file_e_download_automatico_scrivono_la_stessa_statistica(hass, monkeypatch):
    """Il CSV deve finire nella serie del download automatico: stesso
    statistic_id e stesso nome, cosi' un periodo ricaricato sovrascrive
    invece di creare una seconda serie nella Energy Dashboard."""
    from datetime import UTC, datetime

    from custom_components.contatore_letture.distributors.edistribuzione import (
        statistics as edist_stats,
    )

    scrivi = AsyncMock(return_value=None)
    monkeypatch.setattr(edist_stats, "async_scrivi_serie_oraria", scrivi)
    curva_api = [{
        "readings": {"sampleDate": "20260801", "sampleValues": [{"id": "1", "val": "0.1"}]},
        "sampleFrequency": 15,
        "initialSample": "2026-07-31T22:00:00.000+00:00",
    }]
    await edist_stats.async_import_curva_giornaliera(hass, POD, curva_api)
    await edist_stats.async_import_ore(hass, POD, {datetime(2026, 7, 31, 22, tzinfo=UTC): 0.1})

    (_, pod_api, id_api, nome_api, ore_api), (_, pod_csv, id_csv, nome_csv, ore_csv) = (
        c.args for c in scrivi.await_args_list
    )
    assert (pod_api, id_api, nome_api) == (pod_csv, id_csv, nome_csv)
    assert ore_api == ore_csv
