"""La diagnostica scaricabile non contiene credenziali ne' dati fiscali."""
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import Mock

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.diagnostics import (
    async_get_config_entry_diagnostics,
)

SEGRETI = (
    "CLIENT-ID-1",
    "SECRET-ID-1",
    "RSSMRA80A01H501U",
    "TICKET-PENDENTE",
    "TICKET-IN-CORSO",
    "XMzKH12345",
    "mario@example.com",
    "Password1!",
    "REFRESH-1",
)


async def test_diagnostica_senza_credenziali(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Duereti (1 POD)",
        data={
            "distributor": "duereti",
            "client_id": "CLIENT-ID-1",
            "secret_id": "SECRET-ID-1",
            "pods": [{"pod": "IT001E00000001", "df": "RSSMRA80A01H501U"}],
            "pending_ticket": "TICKET-PENDENTE",
            "email": "mario@example.com",
            "password": "Password1!",
            "refresh_token": "REFRESH-1",
        },
        options={"orario_richiesta": "19:00"},
    )
    entry.add_to_hass(hass)
    coordinator = Mock(spec=DataUpdateCoordinator)
    coordinator.last_update_success = False
    coordinator.update_interval = timedelta(hours=1)
    coordinator.last_exception = Exception(
        "Esiste già un token registrato dall'utente, token: XMzKH12345 (mario@example.com)"
    )
    coordinator.data = {"stato": "in attesa del file", "ticket": "TICKET-IN-CORSO"}
    hass.data[DOMAIN] = {entry.entry_id: coordinator}

    risultato = await async_get_config_entry_diagnostics(hass, entry)
    testo = json.dumps(risultato, ensure_ascii=False)

    for segreto in SEGRETI:
        assert segreto not in testo
    assert "IT001E00000001" in testo
    assert risultato["voce"]["opzioni"] == {"orario_richiesta": "19:00"}
    assert risultato["coordinator"]["dati"]["stato"] == "in attesa del file"
    assert risultato["coordinator"]["ultimo_errore"].startswith("Exception: Esiste già un token")


async def test_diagnostica_di_una_voce_senza_coordinator(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"distributor": "edistribuzione"})
    entry.add_to_hass(hass)
    hass.data[DOMAIN] = {entry.entry_id: object()}

    risultato = await async_get_config_entry_diagnostics(hass, entry)

    assert risultato["coordinator"] is None
    assert risultato["voce"]["dati"] == {"distributor": "edistribuzione"}
