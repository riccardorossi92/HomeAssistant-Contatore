"""Test del primo aggiornamento rimandato a dopo l'avvio di Home Assistant
(issue #16): durante il bootstrap non va atteso il recorder dentro
async_setup_entry.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture import _async_primo_aggiornamento
from custom_components.contatore_letture.const import DOMAIN


def _coordinator(*, successo: bool = True) -> Mock:
    coordinator = Mock()
    coordinator.async_refresh = AsyncMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.last_update_success = successo
    coordinator.last_exception = None if successo else RuntimeError("giu'")
    return coordinator


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return entry


async def test_durante_l_avvio_il_refresh_non_viene_atteso(hass):
    hass.set_state(CoreState.starting)
    coordinator = _coordinator()

    await _async_primo_aggiornamento(hass, _entry(hass), coordinator, "Ireti")

    coordinator.async_refresh.assert_not_called()


async def test_a_avvio_completato_il_refresh_parte_in_background(hass):
    hass.set_state(CoreState.starting)
    coordinator = _coordinator()
    await _async_primo_aggiornamento(hass, _entry(hass), coordinator, "Ireti")

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    coordinator.async_refresh.assert_awaited_once()


async def test_se_l_entry_viene_scaricata_prima_il_refresh_non_parte(hass):
    hass.set_state(CoreState.starting)
    coordinator = _coordinator()
    entry = _entry(hass)
    await _async_primo_aggiornamento(hass, entry, coordinator, "Ireti")

    await entry._async_process_on_unload(hass)
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    coordinator.async_refresh.assert_not_called()


async def test_a_hass_gia_avviato_il_refresh_e_atteso_subito(hass):
    hass.set_state(CoreState.running)
    coordinator = _coordinator()

    await _async_primo_aggiornamento(hass, _entry(hass), coordinator, "Ireti")

    coordinator.async_refresh.assert_awaited_once()


async def test_sempre_in_background_non_blocca_il_setup_a_hass_avviato(hass):
    # Edyna: un primo ciclo dura minuti, non va atteso dentro async_setup_entry.
    hass.set_state(CoreState.running)
    coordinator = _coordinator()
    fermo = hass.loop.create_future()

    async def _lento():
        await fermo

    coordinator.async_refresh = AsyncMock(side_effect=_lento)

    await _async_primo_aggiornamento(
        hass, _entry(hass), coordinator, "Edyna", sempre_in_background=True
    )

    # La funzione e' tornata mentre il refresh e' ancora in corso.
    await hass.async_block_till_done(wait_background_tasks=False)
    coordinator.async_refresh.assert_awaited_once()
    assert not fermo.done()
    fermo.set_result(None)
    await hass.async_block_till_done()


async def test_sempre_in_background_durante_l_avvio_resta_rimandato(hass):
    hass.set_state(CoreState.starting)
    coordinator = _coordinator()

    await _async_primo_aggiornamento(
        hass, _entry(hass), coordinator, "Edyna", sempre_in_background=True
    )

    coordinator.async_refresh.assert_not_called()


async def test_refresh_fallito_non_solleva_e_scrive_un_avviso(hass, caplog):
    hass.set_state(CoreState.running)
    coordinator = _coordinator(successo=False)

    await _async_primo_aggiornamento(hass, _entry(hass), coordinator, "Ireti")

    assert "Primo aggiornamento dati non riuscito per Ireti" in caplog.text


async def test_riprova_se_fallisce_usa_il_first_refresh_a_hass_avviato(hass):
    hass.set_state(CoreState.running)
    coordinator = _coordinator()
    coordinator.async_config_entry_first_refresh.side_effect = ConfigEntryNotReady

    with pytest.raises(ConfigEntryNotReady):
        await _async_primo_aggiornamento(
            hass, _entry(hass), coordinator, "E-Distribuzione", riprova_se_fallisce=True
        )

    coordinator.async_refresh.assert_not_called()


async def test_riprova_se_fallisce_durante_l_avvio_non_blocca_il_setup(hass):
    hass.set_state(CoreState.starting)
    coordinator = _coordinator()

    await _async_primo_aggiornamento(
        hass, _entry(hass), coordinator, "E-Distribuzione", riprova_se_fallisce=True
    )

    coordinator.async_config_entry_first_refresh.assert_not_called()
    coordinator.async_refresh.assert_not_called()
