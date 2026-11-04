"""Esito del login Areti nel coordinator.

Credenziali rifiutate -> ConfigEntryAuthFailed (Home Assistant avvia il
reauth e smette di riprovare), non UpdateFailed: con UpdateFailed
coordinator_riprova riprovava ogni 30 minuti con la vecchia password,
all'infinito, rischiando di far bloccare l'account.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.coordinator_riprova import INTERVALLO_RIPROVA
from custom_components.contatore_letture.distributors.areti import create_coordinator
from custom_components.contatore_letture.distributors.areti.auth import (
    AretiAuthError,
    AretiInvalidCredentials,
    AretiParsingError,
)
from custom_components.contatore_letture.distributors.areti.const import CONF_PODS

MODULO = "custom_components.contatore_letture.distributors.areti.coordinator"


@pytest.fixture
def coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "areti",
            CONF_EMAIL: "a@b.it",
            CONF_PASSWORD: "x",
            CONF_PODS: ["IT001E90000001"],
        },
    )
    entry.add_to_hass(hass)
    return create_coordinator(hass, entry)


def _login_che_solleva(errore: Exception):
    auth = Mock()
    auth.async_login = AsyncMock(side_effect=errore)
    sessione = Mock()
    sessione.close = AsyncMock()
    return (
        patch(f"{MODULO}.async_create_session", AsyncMock(return_value=sessione)),
        patch(f"{MODULO}.AretiAuthClient", Mock(return_value=auth)),
    )


async def test_credenziali_rifiutate_avviano_il_reauth(coordinator):
    sessione, auth = _login_che_solleva(AretiInvalidCredentials("Email o password non valida."))
    with sessione, auth, pytest.raises(ConfigEntryAuthFailed, match="non valide"):
        await coordinator._async_login()


async def test_credenziali_rifiutate_non_abbassano_l_intervallo_di_riprova(coordinator):
    """ConfigEntryAuthFailed attraversa coordinator_riprova senza portare
    l'intervallo a 30 minuti: niente nuovi tentativi con la vecchia password."""
    intervallo_normale = coordinator.update_interval
    coordinator._async_login = AsyncMock(side_effect=ConfigEntryAuthFailed("x"))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    assert coordinator.update_interval == intervallo_normale
    assert coordinator.update_interval != INTERVALLO_RIPROVA


@pytest.mark.parametrize(
    "errore",
    [AretiAuthError("ticket-exchange senza sid"), AretiParsingError("fwuid non trovato")],
)
async def test_altri_errori_di_login_restano_update_failed(coordinator, errore):
    sessione, auth = _login_che_solleva(errore)
    with sessione, auth, pytest.raises(UpdateFailed, match="Login Areti fallito"):
        await coordinator._async_login()
