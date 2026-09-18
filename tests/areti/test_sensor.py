"""Test dei sensori diagnostici Areti: leggono coordinator.data nella
forma {"by_pod": {pod: {...}}} (stesso schema di E-Distribuzione).
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

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


def test_consumo_mese_arrotonda_a_tre_decimali():
    coord = _coord(
        {POD: {"kwh_ultimo_mese_importato": 1.23456, "ultimo_mese_importato": "2026-08"}}
    )
    sensore = s.AretiConsumoMeseSensor(coord, _entry(), POD)
    assert sensore.native_value == 1.235
    assert sensore.extra_state_attributes["mese"] == "2026-08"


def test_consumo_mese_senza_dati():
    sensore = s.AretiConsumoMeseSensor(_coord(), _entry(), POD)
    assert sensore.native_value is None


async def test_build_areti_entities_conta_account_piu_due_per_pod(hass):
    entry = _entry(hass, pods=[POD, "IT002"])
    coord = SimpleNamespace(data=None, pods=[POD, "IT002"], entry=entry)
    entità = s.build_areti_entities(hass, coord)
    # 1 sull'account + 2 per ciascuno dei 2 POD
    assert len(entità) == 1 + 2 * 2
    assert "AretiPodConfiguratiSensor" in {type(e).__name__ for e in entità}
