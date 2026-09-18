"""Fixture per i test che istanziano l'IretiCoordinator.

Qui serve principalmente per esercitare la coda dei giorni da riprovare
(per-POD): __init__ non fa I/O, il client auth creato resta inutilizzato.
"""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.ireti import create_coordinator
from custom_components.contatore_letture.distributors.ireti.const import (
    CONF_PASSWORD,
    CONF_PODS,
    CONF_USERNAME,
)

POD_A = "IT020E00000000001"
POD_B = "IT020E00000000002"


@pytest.fixture
def make_ireti_coordinator(hass):
    """Factory: IretiCoordinator con una MockConfigEntry agganciata a hass."""

    def _make(*, data=None, pods=None):
        pods = pods if pods is not None else [POD_A]
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "distributor": "ireti",
                CONF_USERNAME: "utente",
                CONF_PASSWORD: "x",
                CONF_PODS: pods,
                **(data or {}),
            },
        )
        entry.add_to_hass(hass)
        return create_coordinator(hass, entry)

    return _make
