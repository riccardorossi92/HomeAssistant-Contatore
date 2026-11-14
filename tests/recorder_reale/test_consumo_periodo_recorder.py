"""Prova con un recorder vero (database in memoria): le external statistics
scritte come fa l'integrazione devono tornare, per giorno, dal lettore dei
sensori di periodo. Verifica il comportamento reale di statistics_during_period,
che i test con il recorder simulato (tests/test_consumo_periodo.py) danno per
scontato. Vive in una cartella a parte perché recorder_mock deve partire prima
di hass (vedi conftest.py qui accanto).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockEntityPlatform
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.contatore_letture import consumo_periodo as cp
from custom_components.contatore_letture.const import DOMAIN

STATISTIC_ID = "contatore_letture:it001e00000009_energia"


@pytest.fixture(autouse=True)
def _fuso_roma():
    originale = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Rome"))
    yield
    dt_util.set_default_time_zone(originale)


async def _attendi(condizione, timeout: float = 5.0) -> None:
    """L'aggiornamento dei sensori gira come task di fondo della config entry,
    che async_block_till_done non aspetta: si attende l'effetto."""
    async with asyncio.timeout(timeout):
        while not condizione():
            await asyncio.sleep(0.01)


async def _scrivi_ore(hass, ore: dict[datetime, float]) -> None:
    """Scrive external statistics orarie come fa l'integrazione (somma
    progressiva da zero) e aspetta che il recorder le abbia salvate."""
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_add_external_statistics

    somma = 0.0
    stats = []
    for inizio in sorted(ore):
        somma += ore[inizio]
        stats.append({"start": inizio, "state": ore[inizio], "sum": somma})
    async_add_external_statistics(
        hass,
        {
            "has_mean": False,
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": "test",
            "source": DOMAIN,
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": "kWh",
            "unit_class": "energy",
        },
        stats,
    )
    await async_wait_recording_done(hass)


async def test_somma_per_giorno_locale_con_baseline_precedente(hass):
    """La lettura ritorna i kWh per data locale ed esclude ciò che precede `dal`."""
    await _scrivi_ore(
        hass,
        {
            datetime(2026, 10, 31, 22, 0, tzinfo=UTC): 10.0,  # 31/10 locale: prima di `dal`
            datetime(2026, 10, 31, 23, 0, tzinfo=UTC): 1.0,  # mezzanotte locale del 1/11
            datetime(2026, 11, 1, 10, 0, tzinfo=UTC): 2.0,
            datetime(2026, 11, 3, 5, 0, tzinfo=UTC): 4.0,  # il 2 novembre non ha dati
        },
    )

    kwh = await cp.leggi_consumi_giornalieri(hass, STATISTIC_ID, date(2026, 11, 1))
    assert kwh == {date(2026, 11, 1): pytest.approx(3.0), date(2026, 11, 3): pytest.approx(4.0)}
    assert date(2026, 11, 2) not in kwh


async def test_sensori_end_to_end_stati_attributi_e_giro_del_coordinator(hass):
    """Le tre entità in una piattaforma vera, con statistiche vere."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="x", entry_id="abc")
    entry.add_to_hass(hass)
    ascoltatori = []

    def _aggiungi_ascoltatore(callback):
        ascoltatori.append(callback)
        return lambda: ascoltatori.remove(callback)

    coordinator = SimpleNamespace(async_add_listener=_aggiungi_ascoltatore)

    await _scrivi_ore(
        hass,
        {
            datetime(2026, 1, 15, 10, 0, tzinfo=UTC): 100.0,  # anno, non trimestre
            datetime(2026, 10, 20, 10, 0, tzinfo=UTC): 10.0,  # mese precedente e trimestre
            datetime(2026, 11, 2, 10, 0, tzinfo=UTC): 1.5,
            datetime(2026, 11, 2, 11, 0, tzinfo=UTC): 0.5,
            datetime(2026, 11, 12, 10, 0, tzinfo=UTC): 3.0,
        },
    )

    oggi = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    sensori = cp.crea_sensori_consumo(hass, entry, coordinator, "IT001E00000009", {})
    with patch.object(dt_util, "now", return_value=oggi):
        piattaforma = MockEntityPlatform(hass, domain="sensor", platform_name=DOMAIN)
        await piattaforma.async_add_entities(sensori)
        await hass.async_block_till_done()
        await _attendi(lambda: "dal" in hass.states.get(sensori[3].entity_id).attributes)

        assert len(ascoltatori) == 1  # un solo ascoltatore per POD, non uno per sensore

        stati = {s.entity_id: hass.states.get(s.entity_id) for s in sensori}
        mese, precedente, trimestre, anno = (stati[s.entity_id] for s in sensori)
        assert float(mese.state) == pytest.approx(5.0)
        assert float(precedente.state) == pytest.approx(10.0)
        assert precedente.attributes["dal"] == "2026-10-01"
        assert precedente.attributes["al"] == "2026-10-31"
        assert precedente.attributes["dati_fino_al"] == "2026-10-20"
        assert float(trimestre.state) == pytest.approx(15.0)
        assert float(anno.state) == pytest.approx(115.0)
        assert mese.attributes["unit_of_measurement"] == "kWh"
        assert mese.attributes["device_class"] == "energy"
        assert mese.attributes["dal"] == "2026-11-01"
        assert trimestre.attributes["dal"] == "2026-10-01"
        assert anno.attributes["dal"] == "2026-01-01"
        assert mese.attributes["dati_fino_al"] == "2026-11-12"

        # Arrivano dati nuovi e il coordinator chiude un giro: i sensori si aggiornano.
        await _scrivi_ore(
            hass,
            {
                datetime(2026, 11, 13, 9, 0, tzinfo=UTC): 2.0,
            },
        )
        # _scrivi_ore riscrive con somma ripartita da zero, ma `state` per ora
        # (la sola cosa letta) resta quello giusto; le ore precedenti restano.
        ascoltatori[0]()
        await _attendi(
            lambda: float(hass.states.get(sensori[0].entity_id).state) == pytest.approx(7.0)
        )

    # Rimossi i sensori, i trigger vengono staccati.
    for s in sensori:
        await s.async_remove()
    assert ascoltatori == []


async def test_periodo_senza_dati_resta_sconosciuto(hass):
    """Nessun giorno importato nel mese corrente: unknown, non 0 kWh."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="x", entry_id="abc")
    entry.add_to_hass(hass)
    coordinator = SimpleNamespace(async_add_listener=MagicMock(return_value=MagicMock()))
    await _scrivi_ore(hass, {datetime(2026, 10, 20, 10, 0, tzinfo=UTC): 10.0})

    oggi = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    sensori = cp.crea_sensori_consumo(hass, entry, coordinator, "IT001E00000009", {})
    with patch.object(dt_util, "now", return_value=oggi):
        piattaforma = MockEntityPlatform(hass, domain="sensor", platform_name=DOMAIN)
        await piattaforma.async_add_entities(sensori)
        await hass.async_block_till_done()
        await _attendi(lambda: "dal" in hass.states.get(sensori[0].entity_id).attributes)

    mese, precedente, trimestre, _anno = (hass.states.get(s.entity_id) for s in sensori)
    assert mese.state == "unknown"
    assert mese.attributes["dal"] == "2026-11-01"
    assert mese.attributes["dati_fino_al"] is None
    assert float(precedente.state) == pytest.approx(10.0)
    assert float(trimestre.state) == pytest.approx(10.0)

    for sensore in sensori:  # stacca ascoltatore e timer di mezzanotte
        await sensore.async_remove()
