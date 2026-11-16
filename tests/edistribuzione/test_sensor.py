"""Test dei sensori diagnostici E-Distribuzione: leggono coordinator.data
nella forma {"by_pod": {pod: {...}}}.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

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
        {POD: {"kwh_ultimo_giorno_importato": 1.23456, "ultimo_giorno_importato": "2026-09-02"}}
    )
    sensore = s.EdistribuzioneConsumoGiornoSensor(coord, _entry(), POD)
    assert sensore.native_value == 1.235
    assert sensore.extra_state_attributes["giorno"] == "2026-09-02"


async def test_build_edistribuzione_entities_conta_account_piu_due_per_pod(hass):
    entry = _entry(hass, pods=[POD, "IT002"])
    coord = SimpleNamespace(data=None, pods=[POD, "IT002"], entry=entry)
    entità = s.build_edistribuzione_entities(hass, coord)
    # 1 sull'account + 9 per ciascuno dei 2 POD (2 diagnostici + 4 di consumo + 3 di fascia).
    # L'immissione non c'e': nasce dopo, solo sui POD con impianto.
    assert len(entità) == 1 + 9 * 2
    assert "EdistribuzionePodConfiguratiSensor" in {type(e).__name__ for e in entità}


def _coord_dinamico(hass, by_pod, pods=(POD,)):
    entry = _entry(hass, pods=pods)
    ascoltatori = []

    def _add_listener(cb):
        ascoltatori.append(cb)
        return lambda: ascoltatori.remove(cb)

    coord = SimpleNamespace(
        data={"by_pod": by_pod},
        pods=list(pods),
        entry=entry,
        async_add_listener=_add_listener,
    )
    return coord, ascoltatori


async def test_immissione_solo_dopo_aver_letto_has_plant(hass):
    coord, ascoltatori = _coord_dinamico(hass, {POD: {"con_impianto": False}})
    aggiunte = MagicMock()
    s.async_segui_impianti(hass, coord, aggiunte)
    aggiunte.assert_not_called()  # senza impianto (o getSupplies non ancora letto)

    coord.data = {"by_pod": {POD: {"con_impianto": True}}}
    for cb in list(ascoltatori):
        cb()
    aggiunte.assert_called_once()
    nuove = aggiunte.call_args.args[0]
    # 3 fasce di immissione + 5 sensori di periodo
    assert len(nuove) == 8
    assert sum(isinstance(e, s.EdistribuzioneFasciaSensor) for e in nuove) == 3
    assert {e.unique_id.split("_", 2)[-1] for e in nuove if "immissione" in e.unique_id}

    # I giri successivi non le aggiungono di nuovo.
    for cb in list(ascoltatori):
        cb()
    aggiunte.assert_called_once()


async def test_immissione_gia_nota_al_caricamento_si_crea_subito(hass):
    coord, _ = _coord_dinamico(hass, {POD: {"con_impianto": True}})
    aggiunte = MagicMock()
    s.async_segui_impianti(hass, coord, aggiunte)
    aggiunte.assert_called_once()


async def test_immissione_solo_per_i_pod_con_impianto(hass):
    altro = "IT002E00000009"
    coord, _ = _coord_dinamico(
        hass, {POD: {"con_impianto": True}, altro: {"con_impianto": False}}, pods=[POD, altro]
    )
    aggiunte = MagicMock()
    s.async_segui_impianti(hass, coord, aggiunte)
    assert all(POD in e.unique_id for e in aggiunte.call_args.args[0])


def test_consumo_giorno_mantiene_ultimo_valore_e_giorno():
    """Nei cicli senza dati nuovi il sensore tiene l'ultimo valore ricevuto e
    il giorno a cui si riferisce, non quello ripristinato all'avvio."""
    coord = _coord(
        {POD: {"kwh_ultimo_giorno_importato": 10.0, "ultimo_giorno_importato": "2026-11-14"}}
    )
    sensore = s.EdistribuzioneConsumoGiornoSensor(coord, _entry(), POD)
    sensore._ultimo_valore = 32.225
    sensore._ultimo_giorno = "2026-11-01"
    assert sensore.native_value == 10.0
    assert sensore.extra_state_attributes["giorno"] == "2026-11-14"
    coord.data = {
        "by_pod": {POD: {"kwh_ultimo_giorno_importato": None, "ultimo_giorno_importato": None}}
    }
    assert sensore.native_value == 10.0
    assert sensore.extra_state_attributes["giorno"] == "2026-11-14"
