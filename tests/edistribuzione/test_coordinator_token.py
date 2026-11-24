"""Rinnovo del token nel coordinator: un refresh_token scaduto chiede un
nuovo accesso (reauth), un blocco di E-Distribuzione si riprova e basta."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.contatore_letture.distributors.edistribuzione import (
    const as edist_const,
)
from custom_components.contatore_letture.distributors.edistribuzione import coordinator as mod
from custom_components.contatore_letture.distributors.edistribuzione.auth import (
    EdistribuzioneAppBloccata,
    EdistribuzioneBloccoAntibot,
    EdistribuzioneTokenNonValido,
)
from custom_components.contatore_letture.distributors.edistribuzione.const import (
    CONF_REFRESH_TOKEN,
)

from .conftest import POD_A


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


async def test_giro_senza_chiamate_alle_api_non_rinnova_il_token(hass, make_edist_coordinator):
    """Prima dell'orario, con forniture gia' lette oggi e fasce appena
    tentate, il giro non chiama le API: niente richiesta al token endpoint."""
    coordinator = make_edist_coordinator()
    rinnovo = AsyncMock(return_value=SimpleNamespace(access_token="AT", refresh_token="rt"))
    coordinator._auth.async_refresh_access_token = rinnovo
    coordinator._pod_con_impianto = set()
    coordinator._pod_con_impianto_del = dt_util.now().date()
    coordinator._fasce_tentativo[POD_A] = dt_util.utcnow()

    with (
        patch.object(coordinator, "_prossima_richiesta", AsyncMock(return_value=None)),
        patch.object(mod, "async_get_ultima_data_disponibile", AsyncMock(return_value=None)),
    ):
        await coordinator._async_update_data()

    rinnovo.assert_not_awaited()


async def test_un_solo_rinnovo_per_giro_anche_con_piu_chiamate(hass, make_edist_coordinator):
    coordinator = make_edist_coordinator()
    rinnovo = AsyncMock(return_value=SimpleNamespace(access_token="AT", refresh_token="rt"))
    coordinator._auth.async_refresh_access_token = rinnovo
    coordinator._api.async_get_daily_load_profile = AsyncMock(return_value=[])
    coordinator._fasce_tentativo[POD_A] = dt_util.utcnow()
    ieri = dt_util.now().date() - timedelta(days=1)

    with (
        patch.object(coordinator, "_prossima_richiesta", AsyncMock(return_value=(ieri, ieri))),
        patch.object(mod, "async_get_ultima_data_disponibile", AsyncMock(return_value=None)),
    ):
        await coordinator._async_update_data()  # getSupplies + curva: un rinnovo
        await coordinator._async_update_data()  # solo la curva: un altro rinnovo

    assert rinnovo.await_count == 2
    assert coordinator._api.async_get_supplies.await_count == 1
    assert coordinator._api.async_get_daily_load_profile.await_count == 2


async def test_entry_senza_client_salvato_si_rinnova_con_il_precedente(make_edist_coordinator):
    """Le entry create prima di novembre 2026 hanno un refresh_token del
    client precedente: vale solo con quello."""
    coordinator = make_edist_coordinator()
    coordinator._auth.async_refresh_access_token = AsyncMock(
        return_value=SimpleNamespace(access_token="AT", refresh_token="rt")
    )
    await coordinator._async_ensure_token()
    assert coordinator._auth.async_refresh_access_token.await_args.args == (
        "rt",
        edist_const.OAUTH_CLIENT_ID_PRECEDENTE,
    )


async def test_entry_con_client_salvato_si_rinnova_con_quello(make_edist_coordinator):
    coordinator = make_edist_coordinator(
        data={edist_const.CONF_CLIENT_ID_OAUTH: edist_const.OAUTH_CLIENT_ID}
    )
    coordinator._auth.async_refresh_access_token = AsyncMock(
        return_value=SimpleNamespace(access_token="AT", refresh_token="rt")
    )
    await coordinator._async_ensure_token()
    assert coordinator._auth.async_refresh_access_token.await_args.args == (
        "rt",
        edist_const.OAUTH_CLIENT_ID,
    )
