"""Fixture per i test che istanziano l'EdistribuzioneCoordinator.

Qui serve solo per esercitare la coda dei giorni da riprovare (per-POD):
__init__ non fa I/O, e i client auth/api creati restano inutilizzati.
"""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.edistribuzione import create_coordinator
from custom_components.contatore_letture.distributors.edistribuzione.const import (
    CONF_PODS,
    CONF_REFRESH_TOKEN,
)

POD_A = "IT001E10000001"
POD_B = "IT001E10000002"


@pytest.fixture
def make_edist_coordinator(hass):
    """Factory: EdistribuzioneCoordinator con una MockConfigEntry agganciata a hass."""

    def _make(*, data=None, pods=None):
        pods = pods if pods is not None else [POD_A]
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "distributor": "edistribuzione",
                CONF_REFRESH_TOKEN: "rt",
                CONF_PODS: pods,
                **(data or {}),
            },
        )
        entry.add_to_hass(hass)
        return create_coordinator(hass, entry)

    return _make
