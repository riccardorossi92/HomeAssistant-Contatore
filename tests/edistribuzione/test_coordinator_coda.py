"""Coda dei giorni da riprovare dell'EdistribuzioneCoordinator (per-POD).

Stesso passaggio fatto per pcf_common: abbandono a tempo invece che a
conteggio tentativi, con retrocompatibilita' sui formati precedenti.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from freezegun import freeze_time

from custom_components.contatore_letture.distributors.edistribuzione.const import (
    ABBANDONO_CODA_DOPO_GIORNI,
    CONF_GIORNI_DA_RIPROVARE,
)

from .conftest import POD_A, POD_B

OGGI = "2026-09-15 12:00:00"


@pytest.fixture(autouse=True)
async def _fuso_utc(hass):
    await hass.config.async_set_time_zone("UTC")


async def test_abbandona_i_giorni_troppo_vecchi_per_pod(hass, make_edist_coordinator):
    vecchio = (date(2026, 9, 15) - timedelta(days=ABBANDONO_CODA_DOPO_GIORNI)).isoformat()
    recente = "2026-09-14"
    coordinator = make_edist_coordinator(
        pods=[POD_A],
        data={
            CONF_GIORNI_DA_RIPROVARE: {
                POD_A: {"2026-08-20": vecchio, "2026-09-12": recente},
            }
        },
    )

    with freeze_time(OGGI):
        coordinator._accoda_giorno(POD_A, date(2026, 9, 13))
        code = coordinator._leggi_code()

    assert set(code[POD_A]) == {"2026-09-12", "2026-09-13"}


async def test_migra_dict_di_tentativi_a_data(hass, make_edist_coordinator):
    coordinator = make_edist_coordinator(
        pods=[POD_A, POD_B],
        data={CONF_GIORNI_DA_RIPROVARE: {POD_A: {"2026-09-10": 3}, POD_B: {"2026-09-11": 9}}},
    )

    with freeze_time(OGGI):
        code = coordinator._leggi_code()

    assert code == {
        POD_A: {"2026-09-10": date(2026, 9, 15)},
        POD_B: {"2026-09-11": date(2026, 9, 15)},
    }


async def test_migra_coda_piatta_singolo_pod(hass, make_edist_coordinator):
    """Formato ancora piu' vecchio: una sola coda piatta {data: tentativi},
    da quando la entry gestiva un solo POD."""
    coordinator = make_edist_coordinator(
        pods=[POD_A],
        data={CONF_GIORNI_DA_RIPROVARE: {"2026-09-10": 2, "2026-09-11": 5}},
    )

    with freeze_time(OGGI):
        code = coordinator._leggi_code()

    assert code == {POD_A: {"2026-09-10": date(2026, 9, 15), "2026-09-11": date(2026, 9, 15)}}
