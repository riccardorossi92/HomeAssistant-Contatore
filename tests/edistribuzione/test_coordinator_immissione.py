"""Immissione (A2) accanto al prelievo (A1), per i POD con impianto (issue #13).

A2 si chiede solo dove HasPlant di getSupplies non e' False: True sul POD di
scambio con impianto, chiave assente sul POD di produzione. Non tocca la coda
dei giorni ne' fa fallire l'aggiornamento.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time

from custom_components.contatore_letture.distributors.edistribuzione import coordinator as mod
from custom_components.contatore_letture.distributors.edistribuzione.api import (
    EdistribuzioneApiError,
)

from .conftest import POD_A

OGGI = "2026-09-15 12:00:00"
DOMANI = "2026-09-16 12:00:00"
DATA_DA = date(2026, 9, 10)
DATA_A = date(2026, 9, 12)
GIORNO = date(2026, 9, 14)

CON_IMPIANTO = {"IdPod": POD_A, "HasPlant": True}
SENZA_IMPIANTO = {"IdPod": POD_A, "HasPlant": False}
PRODUZIONE = {"IdPod": POD_A, "MeteringPointType": "M2"}


def _curva(giorni: list[date]) -> list[dict]:
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
    monkeypatch.setattr(
        mod.EdistribuzioneCoordinator, "_async_ensure_token", AsyncMock(return_value=None)
    )


@pytest.fixture
def importa():
    with patch.object(mod, "async_import_curva_giornaliera", AsyncMock(return_value=None)) as mock:
        yield mock


@pytest.fixture
async def coordinator(make_edist_coordinator, _senza_token):
    """Coordinator con API finta: POD con impianto e una curva per ogni
    richiesta; i test cambiano i mock dove serve."""
    coord = make_edist_coordinator(pods=[POD_A])
    coord._api.async_get_supplies = AsyncMock(return_value=[CON_IMPIANTO])
    coord._api.async_get_daily_load_profile = AsyncMock(return_value=_curva([DATA_DA]))
    return coord


def _grandezze_richieste(coordinator) -> list[str]:
    return [
        c.kwargs["magnitude"] for c in coordinator._api.async_get_daily_load_profile.await_args_list
    ]


def _grandezze_importate(importa) -> list[str]:
    return [c.kwargs["magnitude"] for c in importa.await_args_list]


# --- recupera_storico ---------------------------------------------------


@pytest.mark.parametrize("fornitura", [CON_IMPIANTO, PRODUZIONE])
async def test_storico_con_impianto_importa_prelievo_e_immissione(coordinator, importa, fornitura):
    coordinator._api.async_get_supplies.return_value = [fornitura]

    with freeze_time(OGGI):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    assert _grandezze_richieste(coordinator) == ["A1", "A2"]
    assert _grandezze_importate(importa) == ["A1", "A2"]


async def test_storico_senza_impianto_chiede_solo_il_prelievo(coordinator, importa):
    coordinator._api.async_get_supplies.return_value = [SENZA_IMPIANTO]

    with freeze_time(OGGI):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    assert _grandezze_richieste(coordinator) == ["A1"]
    assert _grandezze_importate(importa) == ["A1"]


async def test_storico_errore_sull_immissione_non_fa_fallire_l_azione(coordinator, importa):
    async def _per_grandezza(pod, data_da, data_a, magnitude):
        if magnitude == "A2":
            raise EdistribuzioneApiError("timeout")
        return _curva([DATA_DA])

    coordinator._api.async_get_daily_load_profile = AsyncMock(side_effect=_per_grandezza)

    with freeze_time(OGGI):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    assert _grandezze_importate(importa) == ["A1"]


# --- ciclo automatico ---------------------------------------------------


@pytest.fixture
async def ciclo(coordinator):
    """Il ciclo automatico chiede il giorno GIORNO e basta."""
    with (
        patch.object(
            coordinator, "_prossima_richiesta", AsyncMock(return_value=(GIORNO, GIORNO))
        ),
        patch.object(mod, "async_get_ultima_data_disponibile", AsyncMock(return_value=None)),
    ):
        coordinator._api.async_get_daily_load_profile = AsyncMock(return_value=_curva([GIORNO]))
        yield coordinator


async def test_ciclo_con_impianto_importa_anche_l_immissione(ciclo, importa):
    with freeze_time(OGGI):
        await ciclo._async_update_data()

    assert _grandezze_richieste(ciclo) == ["A1", "A2"]
    assert _grandezze_importate(importa) == ["A1", "A2"]


async def test_ciclo_senza_impianto_non_chiede_l_immissione(ciclo, importa):
    ciclo._api.async_get_supplies.return_value = [SENZA_IMPIANTO]

    with freeze_time(OGGI):
        await ciclo._async_update_data()

    assert _grandezze_richieste(ciclo) == ["A1"]


async def test_ciclo_errore_sull_immissione_non_fa_fallire_ne_accoda(ciclo, importa):
    async def _per_grandezza(pod, data_da, data_a, magnitude):
        if magnitude == "A2":
            raise EdistribuzioneApiError("503")
        return _curva([GIORNO])

    ciclo._api.async_get_daily_load_profile = AsyncMock(side_effect=_per_grandezza)

    with freeze_time(OGGI):
        dati = await ciclo._async_update_data()

    assert _grandezze_importate(importa) == ["A1"]
    assert ciclo._leggi_code() == {}
    assert dati["by_pod"][POD_A]["kwh_ultimo_giorno_importato"] == pytest.approx(0.5)


async def test_ciclo_senza_prelievo_il_giorno_va_in_coda_e_l_immissione_aspetta(ciclo, importa):
    ciclo._api.async_get_daily_load_profile = AsyncMock(return_value=[])

    with freeze_time(OGGI):
        await ciclo._async_update_data()

    assert _grandezze_richieste(ciclo) == ["A1"]
    assert list(ciclo._leggi_code()[POD_A]) == [GIORNO.isoformat()]


# --- lettura delle forniture ---------------------------------------------


async def test_getsupplies_che_fallisce_non_e_memorizzato(coordinator, importa):
    """Con getSupplies in errore si importa solo il prelievo, ma alla
    richiesta dopo si riprova: l'immissione non si perde fino al riavvio."""
    coordinator._api.async_get_supplies = AsyncMock(
        side_effect=[EdistribuzioneApiError("503"), [CON_IMPIANTO]]
    )

    with freeze_time(OGGI):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)
        assert _grandezze_importate(importa) == ["A1"]
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    assert _grandezze_importate(importa) == ["A1", "A1", "A2"]
    assert coordinator._api.async_get_supplies.await_count == 2


async def test_forniture_lette_una_volta_al_giorno(coordinator, importa):
    with freeze_time(OGGI):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)
    assert coordinator._api.async_get_supplies.await_count == 1

    # HasPlant puo' cambiare (impianto installato dopo): il giorno dopo si rilegge.
    coordinator._api.async_get_supplies.return_value = [SENZA_IMPIANTO]
    with freeze_time(DOMANI):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    assert coordinator._api.async_get_supplies.await_count == 2
    assert _grandezze_importate(importa)[-1] == "A1"


async def test_le_forniture_non_finiscono_nella_entry(coordinator, importa):
    """getSupplies contiene codice fiscale e indirizzo: non va salvato."""
    with freeze_time(OGGI):
        await coordinator.async_recupera_storico(DATA_DA, DATA_A)
    assert "supplies" not in coordinator.entry.data
