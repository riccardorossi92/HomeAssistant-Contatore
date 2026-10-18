"""Rinnovo del token nel coordinator: un refresh_token scaduto chiede un
nuovo accesso (reauth), un blocco di E-Distribuzione si riprova e basta."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.contatore_letture.distributors.edistribuzione.auth import (
    EdistribuzioneAppBloccata,
    EdistribuzioneBloccoAntibot,
    EdistribuzioneTokenNonValido,
)
from custom_components.contatore_letture.distributors.edistribuzione.const import (
    CONF_REFRESH_TOKEN,
)


async def test_refresh_token_scaduto_chiede_un_nuovo_accesso(make_edist_coordinator):
    coordinator = make_edist_coordinator()
    coordinator._auth.async_refresh_access_token = AsyncMock(
        side_effect=EdistribuzioneTokenNonValido("invalid_grant")
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_ensure_token()


@pytest.mark.parametrize(
    "errore",
    [EdistribuzioneAppBloccata("app_blocked"), EdistribuzioneBloccoAntibot("Imperva")],
)
async def test_blocco_di_e_distribuzione_si_riprova(make_edist_coordinator, errore):
    coordinator = make_edist_coordinator()
    coordinator._auth.async_refresh_access_token = AsyncMock(side_effect=errore)
    with pytest.raises(UpdateFailed):
        await coordinator._async_ensure_token()


async def test_nuovo_refresh_token_salvato(make_edist_coordinator):
    coordinator = make_edist_coordinator()
    coordinator._auth.async_refresh_access_token = AsyncMock(
        return_value=SimpleNamespace(access_token="AT", refresh_token="rt2")
    )
    await coordinator._async_ensure_token()
    assert coordinator.entry.data[CONF_REFRESH_TOKEN] == "rt2"
