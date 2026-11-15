"""Mese precedente per fascia (F1-F3) di E-Distribuzione."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.edistribuzione import sensor as s
from custom_components.contatore_letture.distributors.edistribuzione.api import (
    EdistribuzioneApiError,
)
from custom_components.contatore_letture.distributors.edistribuzione.fasce import (
    fasce_da_risposta,
    mese_precedente,
)

from .conftest import POD_A

# Risposta reale di querymonthlytimeofuse (ottobre 2026).
A1 = [
    {
        "readings": {
            "energyType": "A1",
            "energyT1": "10.587",
            "energyT2": "27.837",
            "energyT3": "77.366",
            "energyT4": "115.790",
        },
        "yearMonth": "202610",
    }
]
A2 = [
    {
        "readings": {
            "energyType": "A2",
            "energyT1": "17.171",
            "energyT2": "0.081",
            "energyT3": "0.912",
            "energyT4": "18.164",
        },
        "yearMonth": "202610",
    }
]
OGGI = "2026-11-15 12:00:00"


@pytest.mark.parametrize(
    ("oggi", "atteso"),
    [
        (date(2026, 11, 15), (date(2026, 10, 1), date(2026, 10, 31), "202610")),
        (date(2026, 11, 1), (date(2026, 10, 1), date(2026, 10, 31), "202610")),
        (date(2027, 1, 20), (date(2026, 12, 1), date(2026, 12, 31), "202612")),
        (date(2028, 3, 1), (date(2028, 2, 1), date(2028, 2, 29), "202802")),
    ],
)
def test_mese_precedente(oggi, atteso):
    assert mese_precedente(oggi) == atteso


def test_fasce_da_risposta_reale():
    assert fasce_da_risposta(A1, "202610") == {"F1": 10.587, "F2": 27.837, "F3": 77.366}
    assert fasce_da_risposta(A2, "202610") == {"F1": 17.171, "F2": 0.081, "F3": 0.912}


def test_fasce_da_risposta_mese_non_pubblicato_non_e_zero():
    assert fasce_da_risposta([], "202611") is None
    assert fasce_da_risposta(A1, "202611") is None


def test_fasce_da_risposta_contratto_con_meno_fasce():
    righe = [{"readings": {"energyT1": "4.0", "energyT2": None, "energyT4": "4.0"}, "yearMonth": "202610"}]
    assert fasce_da_risposta(righe, "202610") == {"F1": 4.0}
    assert fasce_da_risposta([{"readings": {}, "yearMonth": "202610"}], "202610") is None


# --- coordinator --------------------------------------------------------


@pytest.fixture(autouse=True)
async def _fuso_utc(hass):
    await hass.config.async_set_time_zone("UTC")


@pytest.fixture
async def coordinator(make_edist_coordinator):
    coord = make_edist_coordinator(pods=[POD_A])
    coord._api.async_get_supplies = AsyncMock(return_value=[{"IdPod": POD_A, "HasPlant": True}])

    async def _tou(pod, inizio, fine, magnitude="A1"):
        return A1 if magnitude == "A1" else A2

    coord._api.async_get_monthly_time_of_use = AsyncMock(side_effect=_tou)
    return coord


async def test_legge_prelevato_e_immesso_del_mese_precedente(coordinator):
    with freeze_time(OGGI):
        await coordinator._async_aggiorna_fasce(POD_A)

    chiamate = coordinator._api.async_get_monthly_time_of_use.await_args_list
    assert [(c.args[1], c.args[2], c.kwargs["magnitude"]) for c in chiamate] == [
        (date(2026, 10, 1), date(2026, 10, 31), "A1"),
        (date(2026, 10, 1), date(2026, 10, 31), "A2"),
    ]
    assert coordinator._fasce[POD_A] == {
        "mese": "202610",
        "A1": {"F1": 10.587, "F2": 27.837, "F3": 77.366},
        "A2": {"F1": 17.171, "F2": 0.081, "F3": 0.912},
    }


async def test_senza_impianto_solo_prelevato(coordinator):
    coordinator._api.async_get_supplies.return_value = [{"IdPod": POD_A, "HasPlant": False}]
    with freeze_time(OGGI):
        await coordinator._async_aggiorna_fasce(POD_A)
    magnitudini = [
        c.kwargs["magnitude"] for c in coordinator._api.async_get_monthly_time_of_use.await_args_list
    ]
    assert magnitudini == ["A1"]


async def test_una_volta_ottenuto_non_richiama_fino_al_cambio_mese(coordinator):
    with freeze_time(OGGI):
        await coordinator._async_aggiorna_fasce(POD_A)
        await coordinator._async_aggiorna_fasce(POD_A)
    assert coordinator._api.async_get_monthly_time_of_use.await_count == 2  # A1 + A2, una volta

    # Il mese dopo si riparte (a prescindere dall'intervallo di riprova).
    async def _dicembre(pod, inizio, fine, magnitude="A1"):
        return [{**A1[0], "yearMonth": "202611"}]

    coordinator._api.async_get_monthly_time_of_use = AsyncMock(side_effect=_dicembre)
    with freeze_time("2026-12-02 12:00:00"):
        await coordinator._async_aggiorna_fasce(POD_A)
    assert coordinator._fasce[POD_A]["mese"] == "202611"
    assert "A1" in coordinator._fasce[POD_A]


async def test_mese_non_ancora_pubblicato_si_riprova_dopo_sei_ore(coordinator):
    coordinator._api.async_get_monthly_time_of_use = AsyncMock(return_value=[])
    with freeze_time("2026-11-01 08:00:00"):
        await coordinator._async_aggiorna_fasce(POD_A)
        await coordinator._async_aggiorna_fasce(POD_A)  # subito dopo: niente
    assert coordinator._api.async_get_monthly_time_of_use.await_count == 2  # solo il primo giro
    with freeze_time("2026-11-01 15:00:00"):
        await coordinator._async_aggiorna_fasce(POD_A)
    assert coordinator._api.async_get_monthly_time_of_use.await_count == 4


async def test_errore_api_non_fa_fallire_e_si_riprova(coordinator):
    coordinator._api.async_get_monthly_time_of_use = AsyncMock(
        side_effect=EdistribuzioneApiError("timeout")
    )
    with freeze_time(OGGI):
        await coordinator._async_aggiorna_fasce(POD_A)
    assert coordinator._fasce[POD_A] == {"mese": "202610"}


# --- sensori ------------------------------------------------------------


def _sensore(grandezza, fascia, fasce):
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="x", entry_id="abc")
    coord = SimpleNamespace(
        data={"by_pod": {POD_A: {"fasce": fasce}}} if fasce is not None else None,
        pods=[POD_A],
        entry=entry,
    )
    with patch.object(s.CoordinatorEntity, "__init__", lambda self, c: None):
        sensore = s.EdistribuzioneFasciaSensor(coord, entry, POD_A, grandezza, fascia)
    sensore.coordinator = coord
    return sensore


DATI = {
    "mese": "202610",
    "A1": {"F1": 10.587, "F2": 27.837, "F3": 77.366},
    "A2": {"F1": 17.171, "F2": 0.081, "F3": 0.912},
}


def test_sensori_fascia_id_nomi_e_valori():
    consumo = _sensore("A1", "F2", DATI)
    immissione = _sensore("A2", "F3", DATI)
    assert consumo.unique_id == f"abc_{POD_A}_consumo_f2_mese_precedente"
    assert consumo.name == "Consumo F2 mese precedente"
    assert immissione.unique_id == f"abc_{POD_A}_immissione_f3_mese_precedente"
    assert immissione.name == "Immissione F3 mese precedente"
    with freeze_time(OGGI):
        assert consumo.native_value == 27.837
        assert immissione.native_value == 0.912
        assert consumo.extra_state_attributes == {"mese": "2026-10"}
    assert consumo.state_class is None
    assert consumo.native_unit_of_measurement == "kWh"


def test_sensore_fascia_a_mese_nuovo_torna_sconosciuto():
    sensore = _sensore("A1", "F1", DATI)
    with freeze_time("2026-12-01 09:00:00"):  # il dato di ottobre non e' piu' "mese precedente"
        assert sensore.native_value is None
        assert sensore.extra_state_attributes == {}


def test_sensore_fascia_senza_dati_e_sconosciuto():
    assert _sensore("A1", "F1", None).native_value is None
    with freeze_time(OGGI):
        assert _sensore("A1", "F1", {}).native_value is None
        assert _sensore("A2", "F1", {"mese": "202610", "A1": DATI["A1"]}).native_value is None


async def test_il_sensore_fascia_si_riaggiorna_a_mezzanotte(hass):
    sensore = _sensore("A1", "F1", DATI)
    sensore.hass = hass
    rimuovi = []
    sensore.async_on_remove = rimuovi.append
    sensore.async_write_ha_state = MagicMock()
    annulla = MagicMock()
    with (
        patch.object(s.CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(s, "async_track_time_change", return_value=annulla) as traccia,
    ):
        await sensore.async_added_to_hass()
    traccia.assert_called_once()
    assert traccia.call_args.kwargs == {"hour": 0, "minute": 0, "second": 30}
    assert rimuovi == [annulla]
    traccia.call_args.args[1](None)  # lo scatto della mezzanotte
    sensore.async_write_ha_state.assert_called_once()
