"""Le sessioni Areti aperte dai flow vengono chiuse quando non servono piu'."""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture import config_flow as cf
from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.areti.auth import (
    AretiInvalidCredentials,
    AretiPasswordDaCambiare,
)
from custom_components.contatore_letture.distributors.areti.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PODS,
)

AUTH = "custom_components.contatore_letture.distributors.areti.auth"


def _sessione():
    return Mock(name="sessione")


async def test_il_flow_chiude_la_sessione_areti_quando_viene_rimosso(hass):
    flow = cf.ContatoreLettureConfigFlow()
    flow.hass = hass
    sessione = _sessione()
    flow._areti_session = sessione

    with patch(f"{AUTH}.async_chiudi_sessione", AsyncMock()) as chiudi:
        flow.async_remove()
        await hass.async_block_till_done()

    chiudi.assert_awaited_once_with(sessione)
    assert flow._areti_session is None


async def test_async_remove_senza_sessione_non_fa_niente(hass):
    flow = cf.ContatoreLettureConfigFlow()
    flow.hass = hass

    with patch(f"{AUTH}.async_chiudi_sessione", AsyncMock()) as chiudi:
        flow.async_remove()
        await hass.async_block_till_done()

    chiudi.assert_not_awaited()


async def _reauth_flow(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "areti",
            CONF_EMAIL: "vecchia@b.it",
            CONF_PASSWORD: "vecchia",
            CONF_PODS: ["IT001E90000001"],
        },
    )
    entry.add_to_hass(hass)
    flow = cf.ContatoreLettureConfigFlow()
    flow.hass = hass
    flow._reauth_entry = entry
    return flow


async def test_reauth_chiude_la_sessione_anche_con_credenziali_errate(hass):
    flow = await _reauth_flow(hass)
    sessione = _sessione()
    auth = Mock()
    auth.async_login = AsyncMock(side_effect=AretiInvalidCredentials("no"))

    with (
        patch(f"{AUTH}.async_create_session", AsyncMock(return_value=sessione)),
        patch(f"{AUTH}.AretiAuthClient", Mock(return_value=auth)),
        patch(f"{AUTH}.async_chiudi_sessione", AsyncMock()) as chiudi,
    ):
        risultato = await flow.async_step_areti_reauth_user(
            {"email": "n@b.it", "password": "x"}
        )

    assert risultato["type"] is FlowResultType.FORM
    assert risultato["errors"] == {"base": "invalid_auth_email"}
    chiudi.assert_awaited_once_with(sessione)


async def test_reauth_con_password_da_cambiare_mostra_l_errore_dedicato(hass):
    flow = await _reauth_flow(hass)
    auth = Mock()
    auth.async_login = AsyncMock(side_effect=AretiPasswordDaCambiare("pagina"))

    with (
        patch(f"{AUTH}.async_create_session", AsyncMock(return_value=_sessione())),
        patch(f"{AUTH}.AretiAuthClient", Mock(return_value=auth)),
        patch(f"{AUTH}.async_chiudi_sessione", AsyncMock()),
    ):
        risultato = await flow.async_step_areti_reauth_user({"email": "n@b.it", "password": "x"})

    assert risultato["type"] is FlowResultType.FORM
    assert risultato["errors"] == {"base": "areti_password_da_cambiare"}


async def test_primo_login_con_password_da_cambiare_mostra_l_errore_dedicato(hass):
    flow = cf.ContatoreLettureConfigFlow()
    flow.hass = hass
    auth = Mock()
    auth.async_login = AsyncMock(side_effect=AretiPasswordDaCambiare("pagina"))

    with (
        patch(f"{AUTH}.async_create_session", AsyncMock(return_value=_sessione())),
        patch(f"{AUTH}.AretiAuthClient", Mock(return_value=auth)),
    ):
        risultato = await flow.async_step_areti_user({"email": "n@b.it", "password": "x"})

    assert risultato["type"] is FlowResultType.FORM
    assert risultato["errors"] == {"base": "areti_password_da_cambiare"}
