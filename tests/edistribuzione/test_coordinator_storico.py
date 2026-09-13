"""Esiti dell'azione recupera_storico (E-Distribuzione).

L'azione e' manuale e lanciata dall'interfaccia: se non importa nulla deve
fallire in modo visibile, non finire con un successo apparente e un WARNING
nei log (issue #4).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.exceptions import HomeAssistantError

from custom_components.contatore_letture.distributors.edistribuzione.api import (
    EdistribuzioneApiError,
)

from .conftest import POD_A, POD_B

OGGI = "2026-09-15 12:00:00"
DATA_DA = date(2026, 9, 10)
DATA_A = date(2026, 9, 12)


def _curva(giorni: list[date]) -> list[dict]:
    """Risposta di async_get_daily_load_profile con misure per quei giorni."""
    return [
        {
            "readings": {
                "sampleDate": g.strftime("%Y%m%d"),
                "sampleValues": [{"val": "0.5"}],
            }
        }
        for g in giorni
    ]


@pytest.fixture(autouse=True)
async def _fuso_utc(hass):
    await hass.config.async_set_time_zone("UTC")


@pytest.fixture
def _senza_token(monkeypatch):
    """recupera_storico rinfresca il token prima di chiamare l'API: qui non
    interessa, i test riguardano l'esito del recupero."""
    from custom_components.contatore_letture.distributors.edistribuzione import (
        coordinator as mod,
    )

    monkeypatch.setattr(
        mod.EdistribuzioneCoordinator, "_async_ensure_token", AsyncMock(return_value=None)
    )


@pytest.fixture
def _import_statistiche():
    """Neutralizza la scrittura delle statistiche esterne (richiede il
    recorder): i test verificano l'esito dell'azione, non l'import."""
    with patch(
        "custom_components.contatore_letture.distributors.edistribuzione.coordinator"
        ".async_import_curva_giornaliera",
        AsyncMock(return_value=None),
    ) as mock:
        yield mock


async def test_errore_api_su_tutti_i_pod_fa_fallire_l_azione(
    hass, make_edist_coordinator, _senza_token, _import_statistiche
):
    coordinator = make_edist_coordinator(pods=[POD_A])
    coordinator._api.async_get_daily_load_profile = AsyncMock(
        side_effect=EdistribuzioneApiError("403 dal distributore")
    )

    with freeze_time(OGGI), pytest.raises(HomeAssistantError, match="403 dal distributore"):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)


async def test_risposta_vuota_fa_fallire_l_azione(
    hass, make_edist_coordinator, _senza_token, _import_statistiche
):
    coordinator = make_edist_coordinator(pods=[POD_A])
    coordinator._api.async_get_daily_load_profile = AsyncMock(return_value=[])

    with freeze_time(OGGI), pytest.raises(HomeAssistantError, match="[Nn]essun dato"):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)


async def test_risposta_senza_misure_fa_fallire_l_azione(
    hass, make_edist_coordinator, _senza_token, _import_statistiche
):
    """La risposta c'e' ma nessun giorno contiene campioni: per chi ha
    lanciato l'azione equivale a non aver importato niente."""
    coordinator = make_edist_coordinator(pods=[POD_A])
    coordinator._api.async_get_daily_load_profile = AsyncMock(
        return_value=[{"readings": {"sampleDate": "20260910", "sampleValues": []}}]
    )

    with freeze_time(OGGI), pytest.raises(HomeAssistantError, match="senza misure"):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)


async def test_successo_non_solleva(
    hass, make_edist_coordinator, _senza_token, _import_statistiche
):
    coordinator = make_edist_coordinator(pods=[POD_A])
    coordinator._api.async_get_daily_load_profile = AsyncMock(
        return_value=_curva([DATA_DA, DATA_A])
    )

    with freeze_time(OGGI):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    _import_statistiche.assert_awaited_once()


async def test_fallimento_parziale_resta_un_successo(
    hass, make_edist_coordinator, _senza_token, _import_statistiche
):
    """Con piu' POD, se almeno uno ha importato qualcosa l'azione riesce:
    qualcosa E' stato importato, e il POD fallito resta nei log."""
    coordinator = make_edist_coordinator(pods=[POD_A, POD_B])

    async def _per_pod(pod, data_da, data_a):
        if pod == POD_A:
            raise EdistribuzioneApiError("timeout")
        return _curva([data_da])

    coordinator._api.async_get_daily_load_profile = AsyncMock(side_effect=_per_pod)

    with freeze_time(OGGI):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    _import_statistiche.assert_awaited_once()
