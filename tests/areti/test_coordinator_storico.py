"""Esiti dell'azione recupera_storico (Areti).

Stesso contratto di E-Distribuzione: se non viene importato nessun mese
l'azione deve fallire in modo visibile invece di riuscire in silenzio
(issue #4).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.areti import create_coordinator
from custom_components.contatore_letture.distributors.areti.api import AretiApiError
from custom_components.contatore_letture.distributors.areti.const import CONF_PODS

POD = "IT001E90000001"
DATA_DA = date(2026, 7, 1)
DATA_A = date(2026, 7, 31)


@pytest.fixture
async def coordinator(hass, monkeypatch):
    # AretiCoordinator.__init__ non crea più la sessione (lazy, vedi
    # _async_ensure_session: async_create_session ha bisogno di un
    # executor per l'SSLContext, non eseguibile da un __init__ sincrono -
    # era proprio il bug di "got multiple values for keyword argument
    # 'connector'" quando si usava async_create_clientsession con un
    # connector personalizzato, ora corretto), quindi qui non serve più
    # nessun monkeypatch per costruirlo: _async_login viene comunque
    # sostituito sotto, dato che il login vero aprirebbe una sessione TLS
    # reale verso Areti - qui interessa solo cosa fa il coordinator con
    # l'esito delle chiamate all'API.
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "areti",
            CONF_EMAIL: "a@b.it",
            CONF_PASSWORD: "x",
            CONF_PODS: [POD],
        },
    )
    entry.add_to_hass(hass)
    coord = create_coordinator(hass, entry)
    coord._async_login = AsyncMock(return_value=Mock())
    coord._async_config_pod = AsyncMock(return_value=("BP1", "CF1"))
    return coord


@pytest.fixture
def _import_statistiche():
    with patch(
        "custom_components.contatore_letture.distributors.areti.coordinator"
        ".async_import_curva_mensile",
        AsyncMock(return_value=None),
    ) as mock:
        yield mock


async def test_errore_api_fa_fallire_l_azione(hass, coordinator, _import_statistiche):
    api = await coordinator._async_login()
    api.async_get_misurazioni = AsyncMock(side_effect=AretiApiError("500 dal portale"))

    with pytest.raises(HomeAssistantError, match="500 dal portale"):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)


async def test_nessun_mese_disponibile_fa_fallire_l_azione(
    hass, coordinator, _import_statistiche
):
    api = await coordinator._async_login()
    api.async_get_misurazioni = AsyncMock(return_value=None)

    with pytest.raises(HomeAssistantError, match="[Nn]essun"):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)


async def test_dettaglio_senza_elementi_curve_fa_fallire_l_azione(
    hass, coordinator, _import_statistiche
):
    """dettaglio non-None (es. con 'esitoPosizioneBP' valorizzato) ma senza
    'elementiCurve': per chi ha lanciato l'azione equivale a non aver
    importato niente, non a un successo - stesso caso di E-Distribuzione
    ('risposta ricevuta ma senza misure'), mancava qui."""
    api = await coordinator._async_login()
    api.async_get_misurazioni = AsyncMock(
        return_value={"esitoPosizioneBP": {"valore": "100"}, "elementiCurve": []}
    )

    with pytest.raises(HomeAssistantError, match="senza misure"):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)


async def test_successo_non_solleva(hass, coordinator, _import_statistiche):
    api = await coordinator._async_login()
    api.async_get_misurazioni = AsyncMock(
        return_value={"elementiCurve": [{"data": "01/07/2026", "valore": "1,5"}]}
    )

    await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    _import_statistiche.assert_awaited_once()
