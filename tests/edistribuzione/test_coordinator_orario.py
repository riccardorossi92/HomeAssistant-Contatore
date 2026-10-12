"""Orario della richiesta giornaliera: casuale per installazione tra le
16:00 e le 20:00, salvo un orario impostato a mano dalle opzioni."""
from __future__ import annotations

from datetime import date, time
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from custom_components.contatore_letture.distributors.edistribuzione import coordinator as mod
from custom_components.contatore_letture.distributors.edistribuzione.const import (
    CONF_DATA_INSTALLAZIONE,
    CONF_ORA_RICHIESTA,
    CONF_ORARIO_RICHIESTA,
    orario_casuale,
)

from .conftest import POD_A


@pytest.fixture(autouse=True)
async def _fuso_utc(hass):
    await hass.config.async_set_time_zone("UTC")


def test_orario_casuale_resta_tra_16_e_20():
    with patch("random.randint", side_effect=[16 * 60, 20 * 60, 16 * 60 + 21]):
        assert [orario_casuale(), orario_casuale(), orario_casuale()] == [
            "16:00",
            "20:00",
            "16:21",
        ]
    for _ in range(200):
        assert "16:00" <= orario_casuale() <= "20:00"


async def test_entry_senza_orario_ne_riceve_uno_casuale_e_lo_tiene(hass, make_edist_coordinator):
    coordinator = make_edist_coordinator()
    orario = coordinator.entry.options[CONF_ORARIO_RICHIESTA]
    assert "16:00" <= orario <= "20:00"
    assert coordinator._orario_richiesta == time.fromisoformat(orario)

    # Un nuovo coordinator sulla stessa entry (riavvio) non lo cambia.
    secondo = mod.EdistribuzioneCoordinator(hass, coordinator.entry)
    assert secondo.entry.options[CONF_ORARIO_RICHIESTA] == orario


async def test_orario_manuale_vince_sul_casuale(hass, make_edist_coordinator):
    coordinator = make_edist_coordinator()
    hass.config_entries.async_update_entry(
        coordinator.entry, options={CONF_ORARIO_RICHIESTA: "21:10"}
    )
    mod.EdistribuzioneCoordinator(hass, coordinator.entry)
    assert coordinator.entry.options == {CONF_ORARIO_RICHIESTA: "21:10"}
    assert coordinator._orario_richiesta == time(21, 10)


async def test_vecchia_ora_manuale_resta_valida(hass, make_edist_coordinator):
    coordinator = make_edist_coordinator()
    hass.config_entries.async_update_entry(coordinator.entry, options={CONF_ORA_RICHIESTA: 22})
    nuovo = mod.EdistribuzioneCoordinator(hass, coordinator.entry)
    assert coordinator.entry.options == {CONF_ORA_RICHIESTA: 22}
    assert nuovo._orario_richiesta == time(22)


@pytest.mark.parametrize(("adesso", "attesa"), [("16:20", None), ("16:21", (date(2026, 9, 14),) * 2)])
async def test_richiesta_parte_dal_minuto_impostato(hass, make_edist_coordinator, adesso, attesa):
    coordinator = make_edist_coordinator(data={CONF_DATA_INSTALLAZIONE: "2026-09-01"})
    hass.config_entries.async_update_entry(
        coordinator.entry, options={CONF_ORARIO_RICHIESTA: "16:21"}
    )
    with (
        freeze_time(f"2026-09-15 {adesso}:00"),
        patch.object(mod, "async_get_ultima_data_disponibile", return_value=None),
    ):
        assert await coordinator._prossima_richiesta(POD_A) == attesa
