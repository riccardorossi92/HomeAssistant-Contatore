"""Sensore "Consumo ultimo giorno importato" dei distributori che lo leggono
da coordinator.data["by_pod"][pod] (E-Distribuzione, Edyna, Ireti, SET
Distribuzione): stesso comportamento per tutti.

Valore e giorno si aggiornano solo a un giro del coordinator con un dato
nuovo; nei giri senza dati restano gli ultimi noti, e dopo un riavvio si
ripristinano dallo stato salvato.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.edistribuzione import sensor as edist
from custom_components.contatore_letture.distributors.edyna import sensor as edyna
from custom_components.contatore_letture.distributors.ireti import sensor as ireti
from custom_components.contatore_letture.distributors.setdistribuzione import sensor as setd

POD = "IT001E00000009"
NUOVO = {"kwh_ultimo_giorno_importato": 10.0, "ultimo_giorno_importato": "2026-11-14"}
VUOTO = {"kwh_ultimo_giorno_importato": None, "ultimo_giorno_importato": None}

SENSORI = pytest.mark.parametrize(
    "modulo, classe",
    [
        (edist, "EdistribuzioneConsumoGiornoSensor"),
        (edyna, "EdynaConsumoGiornoSensor"),
        (ireti, "IretiConsumoGiornoSensor"),
        (setd, "SetDistribuzioneConsumoGiornoSensor"),
    ],
    ids=["edistribuzione", "edyna", "ireti", "setdistribuzione"],
)


def _sensore(modulo, classe, dati_pod):
    coord = SimpleNamespace(
        data={"by_pod": {POD: dati_pod}} if dati_pod is not None else None,
        pods=[POD],
        entry=None,
    )
    entry = MockConfigEntry(domain=DOMAIN, data={"pods": [POD]}, title="x")
    return getattr(modulo, classe)(coord, entry, POD), coord


def _giro_coordinator(sensore):
    sensore.async_write_ha_state = MagicMock()
    sensore._handle_coordinator_update()
    sensore.async_write_ha_state.assert_called_once()


async def _aggiungi(modulo, sensore, ultimo_stato):
    with (
        patch.object(modulo.CoordinatorEntity, "async_added_to_hass", AsyncMock()),
        patch.object(sensore, "async_get_last_state", AsyncMock(return_value=ultimo_stato)),
    ):
        await sensore.async_added_to_hass()


RIPRISTINATO = SimpleNamespace(state="32.225", attributes={"giorno": "2026-11-01"})


@SENSORI
def test_giro_con_dati_poi_senza_mantiene_valore_e_giorno(modulo, classe):
    sensore, coord = _sensore(modulo, classe, {**NUOVO, "kwh_ultimo_giorno_importato": 10.12345})
    _giro_coordinator(sensore)
    assert sensore.native_value == 10.123
    assert sensore.extra_state_attributes == {"giorno": "2026-11-14"}

    coord.data = {"by_pod": {POD: VUOTO}}
    _giro_coordinator(sensore)
    assert sensore.native_value == 10.123
    assert sensore.extra_state_attributes == {"giorno": "2026-11-14"}


@SENSORI
def test_leggere_lo_stato_non_lo_modifica(modulo, classe):
    sensore, _ = _sensore(modulo, classe, NUOVO)
    assert sensore.native_value is None
    assert sensore.extra_state_attributes == {"giorno": None}


@SENSORI
async def test_riavvio_senza_dati_ripristina_valore_e_giorno(modulo, classe):
    sensore, coord = _sensore(modulo, classe, None)
    await _aggiungi(modulo, sensore, RIPRISTINATO)
    assert sensore.native_value == 32.225
    assert sensore.extra_state_attributes == {"giorno": "2026-11-01"}

    coord.data = {"by_pod": {POD: VUOTO}}
    _giro_coordinator(sensore)
    assert sensore.native_value == 32.225


@SENSORI
async def test_dato_del_coordinator_vince_sul_ripristinato(modulo, classe):
    sensore, _ = _sensore(modulo, classe, NUOVO)
    await _aggiungi(modulo, sensore, RIPRISTINATO)
    assert sensore.native_value == 10.0
    assert sensore.extra_state_attributes == {"giorno": "2026-11-14"}
