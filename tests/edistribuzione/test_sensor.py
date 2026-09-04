"""Test dei sensori diagnostici E-Distribuzione: leggono coordinator.data
nella forma {"by_pod": {pod: {...}}}.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.edistribuzione import sensor as s

POD = "IT001E00000009"


def _entry(hass=None, pods=(POD,)):
    entry = MockConfigEntry(domain=DOMAIN, data={"pods": list(pods)}, title="E-Distribuzione")
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
    sensore = s.EdistribuzionePodConfiguratiSensor(_coord(pods=[POD, "IT002"]), _entry())
    assert sensore.native_value == 2
    assert sensore.extra_state_attributes["pods"] == [POD, "IT002"]


def test_ultima_data_disponibile():
    coord = _coord({POD: {"ultima_data_disponibile": "2026-09-03"}})
    sensore = s.EdistribuzioneUltimaDataDisponibileSensor(coord, _entry(), POD)
    assert sensore.native_value == date(2026, 9, 3)


def test_ultima_data_disponibile_senza_dati():
    sensore = s.EdistribuzioneUltimaDataDisponibileSensor(_coord(), _entry(), POD)
    assert sensore.native_value is None


def test_consumo_giorno_arrotonda_a_tre_decimali():
    coord = _coord(
        {POD: {"kwh_ultimo_giorno_importato": 1.23456, "ultimo_giorno_curva_richiesto": "2026-09-02"}}
    )
    sensore = s.EdistribuzioneConsumoGiornoSensor(coord, _entry(), POD)
    assert sensore.native_value == 1.235
    assert sensore.extra_state_attributes["giorno"] == "2026-09-02"


async def test_build_edistribuzione_entities_conta_account_piu_due_per_pod(hass):
    entry = _entry(hass, pods=[POD, "IT002"])
    coord = SimpleNamespace(data=None, pods=[POD, "IT002"], entry=entry)
    entità = s.build_edistribuzione_entities(hass, coord)
    # 1 sull'account + 2 per ciascuno dei 2 POD
    assert len(entità) == 1 + 2 * 2
    assert "EdistribuzionePodConfiguratiSensor" in {type(e).__name__ for e in entità}
