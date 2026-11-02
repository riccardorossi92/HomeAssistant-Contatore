"""Test del ritentativo a breve dopo un aggiornamento fallito (issue #17)."""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.contatore_letture.coordinator_riprova import (
    INTERVALLO_RIPROVA,
    CoordinatorRiprovaDopoErrore,
)

_LOGGER = logging.getLogger(__name__)
GIORNALIERO = timedelta(hours=24)


class _Coordinator(CoordinatorRiprovaDopoErrore):
    def __init__(self, hass) -> None:
        super().__init__(hass, _LOGGER, name="test", update_interval=GIORNALIERO)
        self.esiti: list = []

    async def _async_aggiorna(self) -> dict:
        esito = self.esiti.pop(0)
        if isinstance(esito, Exception):
            raise esito
        return esito


async def test_dopo_un_errore_si_riprova_a_breve(hass):
    coordinator = _Coordinator(hass)
    coordinator.esiti = [UpdateFailed("rete giu'")]

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.update_interval == INTERVALLO_RIPROVA


async def test_a_riuscita_torna_l_intervallo_normale(hass):
    coordinator = _Coordinator(hass)
    coordinator.esiti = [UpdateFailed("rete giu'"), {"by_pod": {}}]

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.update_interval == GIORNALIERO


async def test_errori_ripetuti_restano_a_intervallo_breve(hass):
    coordinator = _Coordinator(hass)
    coordinator.esiti = [UpdateFailed("1"), RuntimeError("2")]

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert coordinator.update_interval == INTERVALLO_RIPROVA


async def test_credenziali_non_valide_non_abbassano_l_intervallo(hass):
    coordinator = _Coordinator(hass)
    coordinator.esiti = [ConfigEntryAuthFailed("credenziali")]

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    assert coordinator.update_interval == GIORNALIERO


async def test_senza_errori_l_intervallo_non_cambia(hass):
    coordinator = _Coordinator(hass)
    coordinator.esiti = [{"by_pod": {}}]

    await coordinator.async_refresh()

    assert coordinator.update_interval == GIORNALIERO
