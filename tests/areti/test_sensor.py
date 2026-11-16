"""Test dei sensori diagnostici Areti: leggono coordinator.data nella
forma {"by_pod": {pod: {...}}} (stesso schema di E-Distribuzione).
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.areti import sensor as s

POD = "IT001E00000009"


def _entry(hass=None, pods=(POD,)):
    entry = MockConfigEntry(domain=DOMAIN, data={"pods": list(pods)}, title="Areti")
    if hass is not None:
        entry.add_to_hass(hass)
    return entry


def _coord(by_pod=None, pods=(POD,)):
    return SimpleNamespace(
        data={"by_pod": by_pod} if by_pod is not None else None,
        pods=list(pods),
        entry=None,
    )


def test_pod_configurati():
    sensore = s.AretiPodConfiguratiSensor(_coord(pods=[POD, "IT002"]), _entry())
    assert sensore.native_value == 2
    assert sensore.extra_state_attributes["pods"] == [POD, "IT002"]


def test_ultima_data_disponibile():
    coord = _coord({POD: {"ultima_data_disponibile": "2026-09-03"}})
    sensore = s.AretiUltimaDataDisponibileSensor(coord, _entry(), POD)
    assert sensore.native_value == date(2026, 9, 3)


def test_ultima_data_disponibile_senza_dati():
    sensore = s.AretiUltimaDataDisponibileSensor(_coord(), _entry(), POD)
    assert sensore.native_value is None


def _giro_coordinator(sensore):
    """Simula un aggiornamento del coordinator (la scrittura dello stato e'
    sostituita da un mock: niente hass)."""
    sensore.async_write_ha_state = MagicMock()
    sensore._handle_coordinator_update()


def test_consumo_mese_arrotonda_a_tre_decimali():
    coord = _coord(
        {POD: {"kwh_ultimo_mese_importato": 1.23456, "ultimo_mese_importato": "2026-08"}}
    )
    sensore = s.AretiConsumoMeseSensor(coord, _entry(), POD)
    _giro_coordinator(sensore)
    assert sensore.native_value == 1.235
    assert sensore.extra_state_attributes["mese"] == "2026-08"


def test_consumo_mese_senza_dati():
    sensore = s.AretiConsumoMeseSensor(_coord(), _entry(), POD)
    assert sensore.native_value is None


async def test_build_areti_entities_conta_account_piu_due_per_pod(hass):
    entry = _entry(hass, pods=[POD, "IT002"])
    coord = SimpleNamespace(data=None, pods=[POD, "IT002"], entry=entry)
    entità = s.build_areti_entities(hass, coord)
    # 1 sull'account + 5 per ciascuno dei 2 POD (2 diagnostici + 3 di periodo, senza il mese corrente)
    assert len(entità) == 1 + 5 * 2
    assert "AretiPodConfiguratiSensor" in {type(e).__name__ for e in entità}


def test_consumo_mese_mantiene_ultimo_valore_e_mese():
    """Nei giri senza un mese nuovo restano l'ultimo valore e il suo mese;
    il prossimo mese da importare segue invece il coordinator."""
    coord = _coord(
        {POD: {"kwh_ultimo_mese_importato": 100.0, "ultimo_mese_importato": "2026-09",
               "mese_da_importare": "2026-10"}}
    )
    sensore = s.AretiConsumoMeseSensor(coord, _entry(), POD)
    sensore._ultimo_valore = 80.0
    sensore._ultimo_mese = "2026-08"
    _giro_coordinator(sensore)
    coord.data = {"by_pod": {POD: {"kwh_ultimo_mese_importato": None,
                                   "ultimo_mese_importato": None,
                                   "mese_da_importare": "2026-10"}}}
    _giro_coordinator(sensore)
    assert sensore.native_value == 100.0
    assert sensore.extra_state_attributes == {
        "mese": "2026-09",
        "prossimo_mese_da_importare": "2026-10",
    }
