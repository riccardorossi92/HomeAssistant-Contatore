"""Test dei sensori consumo mese/trimestre/anno corrente (issue #20)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture import consumo_periodo as cp
from custom_components.contatore_letture.const import DOMAIN

POD = "IT001E00000009"


@pytest.fixture(autouse=True)
def _fuso_utc():
    originale = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(UTC)
    yield
    dt_util.set_default_time_zone(originale)


# --- inizio_periodo ----------------------------------------------------------

@pytest.mark.parametrize(
    ("periodo", "oggi", "atteso"),
    [
        ("mese", date(2026, 11, 13), date(2026, 11, 1)),
        ("mese", date(2026, 11, 1), date(2026, 11, 1)),
        ("trimestre", date(2026, 1, 1), date(2026, 1, 1)),
        ("trimestre", date(2026, 3, 31), date(2026, 1, 1)),
        ("trimestre", date(2026, 4, 1), date(2026, 4, 1)),
        ("trimestre", date(2026, 11, 13), date(2026, 10, 1)),
        ("trimestre", date(2026, 12, 31), date(2026, 10, 1)),
        ("anno", date(2026, 11, 13), date(2026, 1, 1)),
    ],
)
def test_inizio_periodo(periodo, oggi, atteso):
    assert cp.inizio_periodo(periodo, oggi) == atteso


def test_inizio_periodo_sconosciuto():
    with pytest.raises(ValueError):
        cp.inizio_periodo("settimana", date(2026, 1, 1))


# --- somma_periodo -----------------------------------------------------------

KWH = {
    date(2026, 9, 30): 5.0,
    date(2026, 10, 1): 1.0,
    date(2026, 10, 15): 2.0,
    date(2026, 11, 2): 4.0,
    date(2026, 11, 12): 0.5,
}


def test_somma_solo_i_giorni_del_periodo():
    totale, ultimo = cp.somma_periodo(KWH, date(2026, 11, 1), date(2026, 11, 13))
    assert totale == pytest.approx(4.5)
    assert ultimo == date(2026, 11, 12)


def test_somma_trimestre_include_i_mesi_precedenti_del_trimestre():
    totale, ultimo = cp.somma_periodo(KWH, date(2026, 10, 1), date(2026, 11, 13))
    assert totale == pytest.approx(7.5)
    assert ultimo == date(2026, 11, 12)


def test_somma_periodo_senza_giorni_non_e_zero():
    # Nessun giorno importato nel periodo: None, non 0 kWh.
    assert cp.somma_periodo(KWH, date(2026, 12, 1), date(2026, 12, 5)) == (None, None)
    assert cp.somma_periodo({}, date(2026, 1, 1), date(2026, 12, 31)) == (None, None)


def test_somma_periodo_zero_kwh_reale_resta_zero():
    # Un giorno importato con consumo 0 è un dato vero, diverso da "nessun dato".
    assert cp.somma_periodo({date(2026, 11, 3): 0.0}, date(2026, 11, 1), date(2026, 11, 13)) == (
        0.0,
        date(2026, 11, 3),
    )


# --- leggi_consumi_giornalieri -----------------------------------------------

async def test_leggi_consumi_giornalieri_somma_le_ore_per_giorno_locale(hass):
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Rome"))
    # 23:00 UTC del 31 ottobre = mezzanotte del 1 novembre a Roma (CET, UTC+1).
    mezzanotte = datetime(2026, 10, 31, 23, 0, tzinfo=UTC).timestamp()
    recorder = MagicMock()
    chiamate = []

    async def _blocca():
        return None

    async def _esegui(funzione, *args):
        chiamate.append((funzione, args))
        return {"contatore_letture:x_energia": [
            {"start": mezzanotte, "state": 1.5},
            {"start": mezzanotte + 3600, "state": 2.0},
            {"start": mezzanotte + 3600 * 2, "state": None},
            {"start": mezzanotte + 86400, "state": 0.25},
        ]}

    recorder.async_block_till_done = _blocca
    recorder.async_add_executor_job = _esegui
    with patch.object(cp, "get_instance", return_value=recorder):
        kwh = await cp.leggi_consumi_giornalieri(hass, "contatore_letture:x_energia", date(2026, 1, 1))

    assert kwh == {date(2026, 11, 1): pytest.approx(3.5), date(2026, 11, 2): pytest.approx(0.25)}
    funzione, args = chiamate[0]
    assert funzione is cp.statistics_during_period
    assert args[1] == dt_util.start_of_local_day(date(2026, 1, 1))  # start
    assert args[2] is None  # end
    assert args[3] == {"contatore_letture:x_energia"}
    assert args[4] == "hour"
    assert args[6] == {"state"}


# --- ConsumiPod + sensori ----------------------------------------------------

def _consumi(hass, entry=None):
    entry = entry or MockConfigEntry(domain=DOMAIN, data={}, title="x")
    coordinator = SimpleNamespace(async_add_listener=MagicMock(return_value=MagicMock()))
    return cp.ConsumiPod(hass, entry, coordinator, POD), entry, coordinator


async def test_aggiorna_calcola_i_tre_periodi(hass):
    consumi, _entry, _coord = _consumi(hass)
    oggi = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    with (
        patch.object(dt_util, "now", return_value=oggi),
        patch.object(cp, "leggi_consumi_giornalieri", return_value=KWH) as lettura,
    ):
        await consumi.async_aggiorna()

    lettura.assert_awaited_once_with(hass, "contatore_letture:it001e00000009_energia", date(2026, 1, 1))
    assert consumi.valori["mese"] == (pytest.approx(4.5), date(2026, 11, 1), date(2026, 11, 12))
    assert consumi.valori["trimestre"] == (pytest.approx(7.5), date(2026, 10, 1), date(2026, 11, 12))
    assert consumi.valori["anno"] == (pytest.approx(12.5), date(2026, 1, 1), date(2026, 11, 12))


async def test_errore_di_lettura_non_cancella_i_valori_precedenti(hass):
    consumi, _entry, _coord = _consumi(hass)
    consumi.valori["mese"] = (1.0, date(2026, 11, 1), date(2026, 11, 2))
    with patch.object(cp, "leggi_consumi_giornalieri", side_effect=RuntimeError("recorder")):
        await consumi.async_aggiorna()
    assert consumi.valori["mese"] == (1.0, date(2026, 11, 1), date(2026, 11, 2))


def test_sensore_prima_della_prima_lettura_e_sconosciuto(hass):
    consumi, entry, _coord = _consumi(hass)
    sensore = cp.ConsumoPeriodoSensor(consumi, entry, POD, "mese", {})
    assert sensore.native_value is None
    assert sensore.extra_state_attributes == {}


def test_sensore_arrotonda_e_espone_gli_attributi(hass):
    consumi, entry, _coord = _consumi(hass)
    consumi.valori["anno"] = (12.34567, date(2026, 1, 1), date(2026, 11, 12))
    sensore = cp.ConsumoPeriodoSensor(consumi, entry, POD, "anno", {})
    assert sensore.native_value == 12.346
    assert sensore.extra_state_attributes == {"dal": "2026-01-01", "dati_fino_al": "2026-11-12"}


def test_sensore_periodo_senza_dati_ha_valore_none_ma_attributo_dal(hass):
    consumi, entry, _coord = _consumi(hass)
    consumi.valori["mese"] = (None, date(2026, 11, 1), None)
    sensore = cp.ConsumoPeriodoSensor(consumi, entry, POD, "mese", {})
    assert sensore.native_value is None
    assert sensore.extra_state_attributes == {"dal": "2026-11-01", "dati_fino_al": None}


def test_crea_sensori_consumo_tre_sensori_con_id_stabili(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="x", entry_id="abc")
    sensori = cp.crea_sensori_consumo(hass, entry, SimpleNamespace(), POD, {"name": "POD"})
    assert [s.unique_id for s in sensori] == [
        f"abc_{POD}_consumo_mese_corrente",
        f"abc_{POD}_consumo_trimestre_corrente",
        f"abc_{POD}_consumo_anno_corrente",
    ]
    assert [s.name for s in sensori] == [
        "Consumo mese corrente",
        "Consumo trimestre corrente",
        "Consumo anno corrente",
    ]
    for s in sensori:
        assert s.device_class == SensorDeviceClass.ENERGY
        assert s.native_unit_of_measurement == "kWh"
        assert s.state_class is None
        assert s.entity_category is None
        assert s.device_info == {"name": "POD"}


async def test_registra_avvia_i_trigger_una_volta_e_li_ferma_con_l_ultimo(hass):
    consumi, entry, coordinator = _consumi(hass)
    entry.add_to_hass(hass)
    sensori = [cp.ConsumoPeriodoSensor(consumi, entry, POD, p, {}) for p in cp.PERIODI]
    with (
        patch.object(consumi, "_pianifica_aggiornamento") as pianifica,
        patch.object(cp, "async_track_time_change", return_value=MagicMock()) as mezzanotte,
    ):
        for s in sensori:
            consumi.registra(s)
        coordinator.async_add_listener.assert_called_once_with(consumi._al_giro_coordinator)
        mezzanotte.assert_called_once()
        assert mezzanotte.call_args.kwargs == {"hour": 0, "minute": 0, "second": 30}
        pianifica.assert_called_once()  # prima lettura, solo al primo sensore

        annulla_coordinator, annulla_orario = consumi._annulla
        consumi.rimuovi(sensori[0])
        consumi.rimuovi(sensori[1])
        annulla_coordinator.assert_not_called()
        consumi.rimuovi(sensori[2])
        annulla_coordinator.assert_called_once()
        annulla_orario.assert_called_once()

