"""Test della logica di aggregazione e import delle statistiche E-Distribuzione."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.contatore_letture.distributors.edistribuzione import statistics as st


@pytest.fixture(autouse=True)
def _fuso_utc():
    originale = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(UTC)
    yield
    dt_util.set_default_time_zone(originale)


def _curva_raw(sample_values: list[dict], initial_sample: str = "2026-08-01T00:00:00.000+00:00", energy_type: str = "A1") -> list[dict]:
    return [
        {
            "readings": {
                "energyType": energy_type,
                "sampleDate": "20260801",
                "sampleValues": sample_values,
            },
            "sampleFrequency": 15,
            "timeType": "CONS",
            "initialSample": initial_sample,
        }
    ]


# --- _aggrega_per_ora ---------------------------------------------------

def test_quattro_quarti_dora_nello_stesso_bucket():
    elementi = _curva_raw([
        {"id": "1", "val": "0.25"},
        {"id": "2", "val": "0.25"},
        {"id": "3", "val": "0.25"},
        {"id": "4", "val": "0.25"},
    ])
    risultato = dict(st._aggrega_per_ora(elementi))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(1.0)


def test_campione_non_valido_scartato():
    elementi = _curva_raw([
        {"id": "1", "val": "non-valido"},
        {"id": "2", "val": "0.5"},
    ])
    risultato = dict(st._aggrega_per_ora(elementi))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(0.5)


def test_initial_sample_non_valido_scartato():
    elementi = _curva_raw([{"id": "1", "val": "0.5"}], initial_sample="non-iso-date")
    assert st._aggrega_per_ora(elementi) == []


# --- async_import_curva_giornaliera -------------------------------------

async def test_import_curva_a1_scrive_energia(hass):
    dati = _curva_raw([{"id": "1", "val": "0.5"}], energy_type="A1")
    with patch(
        "custom_components.contatore_letture.distributors.edistribuzione.statistics.async_scrivi_serie_oraria",
        AsyncMock(return_value=datetime(2026, 8, 1).date()),
    ) as mock_scrivi:
        res = await st.async_import_curva_giornaliera(hass, "IT001E12345678", dati, magnitude="A1")
        assert res == datetime(2026, 8, 1).date()
        mock_scrivi.assert_awaited_once()
        args = mock_scrivi.await_args[0]
        assert args[2] == "contatore_letture:it001e12345678_energia"
        assert args[3] == "E-Distribuzione IT001E12345678"


async def test_import_curva_a2_scrive_immissione(hass):
    dati = _curva_raw([{"id": "1", "val": "0.5"}], energy_type="A2")
    with patch(
        "custom_components.contatore_letture.distributors.edistribuzione.statistics.async_scrivi_serie_oraria",
        AsyncMock(return_value=datetime(2026, 8, 1).date()),
    ) as mock_scrivi:
        res = await st.async_import_curva_giornaliera(hass, "IT001E12345678", dati, magnitude="A2")
        assert res == datetime(2026, 8, 1).date()
        mock_scrivi.assert_awaited_once()
        args = mock_scrivi.await_args[0]
        assert args[2] == "contatore_letture:it001e12345678_immissione"
        assert args[3] == "E-Distribuzione IT001E12345678 Immissione"


async def test_import_curva_scarta_se_energy_type_non_coincide(hass):
    dati = _curva_raw([{"id": "1", "val": "0.5"}], energy_type="A1")
    with patch(
        "custom_components.contatore_letture.distributors.edistribuzione.statistics.async_scrivi_serie_oraria",
        AsyncMock(),
    ) as mock_scrivi:
        res = await st.async_import_curva_giornaliera(hass, "IT001E12345678", dati, magnitude="A2")
        assert res is None
        mock_scrivi.assert_not_awaited()
