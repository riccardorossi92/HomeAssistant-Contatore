"""Test degli helper usati dalle azioni recupera_ticket / recupera_storico:
scelta del coordinator per entry_id e risalita coordinator+POD da un device.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture import (
    _risolvi_coordinator_e_pod_da_device,
    _trova_coordinator,
)
from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.edistribuzione.coordinator import (
    EdistribuzioneCoordinator,
)
from custom_components.contatore_letture.distributors.pcf_common.coordinator import PcfCoordinator


def _pcf() -> Mock:
    return Mock(spec=PcfCoordinator)


# --- _trova_coordinator ---------------------------------------------------

def test_nessuna_istanza_solleva_errore(hass):
    hass.data[DOMAIN] = {}
    with pytest.raises(HomeAssistantError):
        _trova_coordinator(hass, None, (PcfCoordinator,))


def test_una_sola_istanza_viene_restituita_senza_entry_id(hass):
    c = _pcf()
    hass.data[DOMAIN] = {"e1": c}
    assert _trova_coordinator(hass, None, (PcfCoordinator,)) is c


def test_entry_id_esistente_seleziona_quella_giusta(hass):
    c1, c2 = _pcf(), _pcf()
    hass.data[DOMAIN] = {"e1": c1, "e2": c2}
    assert _trova_coordinator(hass, "e2", (PcfCoordinator,)) is c2


def test_entry_id_inesistente_solleva_errore(hass):
    hass.data[DOMAIN] = {"e1": _pcf()}
    with pytest.raises(HomeAssistantError):
        _trova_coordinator(hass, "ignoto", (PcfCoordinator,))


def test_piu_istanze_senza_entry_id_solleva_errore(hass):
    hass.data[DOMAIN] = {"e1": _pcf(), "e2": _pcf()}
    with pytest.raises(HomeAssistantError):
        _trova_coordinator(hass, None, (PcfCoordinator,))


def test_i_coordinator_di_tipo_diverso_sono_ignorati(hass):
    pcf = _pcf()
    hass.data[DOMAIN] = {"e1": pcf, "e2": Mock(spec=EdistribuzioneCoordinator)}
    # un solo PcfCoordinator -> nessuna ambiguità
    assert _trova_coordinator(hass, None, (PcfCoordinator,)) is pcf


# --- _risolvi_coordinator_e_pod_da_device --------------------------------

def test_device_inesistente_solleva_errore(hass):
    hass.data[DOMAIN] = {}
    with pytest.raises(HomeAssistantError):
        _risolvi_coordinator_e_pod_da_device(hass, "device-che-non-esiste")


def test_device_non_di_una_nostra_entry_solleva_errore(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("altra_integrazione", "x")},
    )
    hass.data[DOMAIN] = {}  # la entry non è tra quelle attive dell'integrazione

    with pytest.raises(HomeAssistantError):
        _risolvi_coordinator_e_pod_da_device(hass, device.id)


def test_device_account_pcf_restituisce_coordinator_senza_pod(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )
    coordinator = _pcf()
    hass.data[DOMAIN] = {entry.entry_id: coordinator}

    assert _risolvi_coordinator_e_pod_da_device(hass, device.id) == (coordinator, None)


def test_device_per_pod_edistribuzione_restituisce_anche_il_pod(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    pod = "IT001E00000001"
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_{pod}")},
    )
    coordinator = Mock(spec=EdistribuzioneCoordinator)
    coordinator.pods = [pod]
    hass.data[DOMAIN] = {entry.entry_id: coordinator}

    assert _risolvi_coordinator_e_pod_da_device(hass, device.id) == (coordinator, pod)
