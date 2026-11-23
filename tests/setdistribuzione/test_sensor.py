"""Sensori di immissione SET Distribuzione: nascono solo sui POD con
impianto, dopo il primo giro del coordinator che lo ha letto (come
E-Distribuzione, vedi tests/edistribuzione/test_sensor.py)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.setdistribuzione import sensor as s

POD = "IT999E00000001"


def _coord_dinamico(hass, by_pod, pods=(POD,)):
    entry = MockConfigEntry(domain=DOMAIN, data={"pods": list(pods)}, title="SET Distribuzione")
    entry.add_to_hass(hass)
    ascoltatori = []

    def _add_listener(cb):
        ascoltatori.append(cb)
        return lambda: ascoltatori.remove(cb)

    coord = SimpleNamespace(
        data={"by_pod": by_pod}, pods=list(pods), entry=entry, async_add_listener=_add_listener,
    )
    return coord, ascoltatori


async def test_immissione_solo_dopo_aver_trovato_l_impianto(hass):
    coord, ascoltatori = _coord_dinamico(hass, {POD: {"con_impianto": False}})
    aggiunte = MagicMock()
    s.async_segui_impianti(hass, coord, aggiunte)
    aggiunte.assert_not_called()

    coord.data = {"by_pod": {POD: {"con_impianto": True}}}
    for cb in list(ascoltatori):
        cb()
    aggiunte.assert_called_once()
    nuove = aggiunte.call_args.args[0]
    # 1 immissione ultimo giorno + 5 sensori di periodo.
    assert len(nuove) == 6
    assert sum(isinstance(e, s.SetDistribuzioneImmissioneGiornoSensor) for e in nuove) == 1
    assert all("immissione" in e.unique_id for e in nuove)

    for cb in list(ascoltatori):
        cb()
    aggiunte.assert_called_once()


def test_immissione_giorno_legge_le_chiavi_dell_immissione():
    coord = SimpleNamespace(data={"by_pod": {POD: {
        "kwh_ultimo_giorno_importato": 1.0, "ultimo_giorno_importato": "2026-10-12",
        "kwh_ultimo_giorno_immesso": 10.41749, "ultimo_giorno_immesso": "2026-10-12",
    }}})
    entry = MockConfigEntry(domain=DOMAIN, data={"pods": [POD]})
    sensore = s.SetDistribuzioneImmissioneGiornoSensor(coord, entry, POD)
    sensore._memorizza_ultimo()
    assert sensore.native_value == 10.417
    assert sensore.extra_state_attributes == {"giorno": "2026-10-12"}
    assert sensore.unique_id.endswith(f"_{POD}_immissione_giorno")
