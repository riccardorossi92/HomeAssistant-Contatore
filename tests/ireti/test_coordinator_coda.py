"""Coda dei giorni da riprovare dell'IretiCoordinator (per-POD).

Stesso meccanismo di edistribuzione (abbandono a tempo, non a conteggio
tentativi) - vedi tests/edistribuzione/test_coordinator_coda.py. Niente
test di migrazione da formati precedenti: a differenza di edistribuzione,
questa coda è nuova (la prima versione del coordinator Ireti usava una
finestra fissa senza nessuno stato persistito), quindi non esiste un
formato vecchio da cui migrare.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from freezegun import freeze_time

from custom_components.contatore_letture.distributors.ireti.const import (
    ABBANDONO_CODA_DOPO_GIORNI,
    CONF_GIORNI_DA_RIPROVARE,
    MAX_GIORNI_IN_CODA,
)

from .conftest import POD_A, POD_B

OGGI = "2026-09-15 12:00:00"


@pytest.fixture(autouse=True)
async def _fuso_utc(hass):
    await hass.config.async_set_time_zone("UTC")


async def test_abbandona_i_giorni_troppo_vecchi_per_pod(hass, make_ireti_coordinator):
    vecchio = (date(2026, 9, 15) - timedelta(days=ABBANDONO_CODA_DOPO_GIORNI)).isoformat()
    recente = "2026-09-14"
    coordinator = make_ireti_coordinator(
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


async def test_accodare_due_volte_lo_stesso_giorno_non_ne_resetta_il_timer(
    hass, make_ireti_coordinator
):
    coordinator = make_ireti_coordinator(pods=[POD_A])

    with freeze_time("2026-09-10 12:00:00"):
        coordinator._accoda_giorno(POD_A, date(2026, 9, 9))

    with freeze_time("2026-09-16 12:00:00"):
        # Sono già passati 6 giorni: se il timer si resettasse qui, il
        # giorno non verrebbe mai abbandonato (finché arriva un ciclo che
        # non lo riaccoda).
        coordinator._accoda_giorno(POD_A, date(2026, 9, 9))
        code = coordinator._leggi_code()
        assert code[POD_A]["2026-09-09"] == date(2026, 9, 10)

    with freeze_time("2026-09-18 12:00:00"):
        # 8 giorni dal primo inserimento (> ABBANDONO_CODA_DOPO_GIORNI=7):
        # deve sparire al primo _scrivi_code successivo.
        coordinator._scrivi_code(coordinator._leggi_code())
        code = coordinator._leggi_code()
        assert POD_A not in code or "2026-09-09" not in code[POD_A]


async def test_rimuovi_dalla_coda_toglie_solo_i_giorni_indicati(hass, make_ireti_coordinator):
    coordinator = make_ireti_coordinator(
        pods=[POD_A],
        data={CONF_GIORNI_DA_RIPROVARE: {POD_A: {"2026-09-10": "2026-09-10", "2026-09-11": "2026-09-11"}}},
    )

    with freeze_time(OGGI):
        coordinator._rimuovi_dalla_coda(POD_A, [date(2026, 9, 10)])
        code = coordinator._leggi_code()

    assert set(code.get(POD_A, {})) == {"2026-09-11"}


async def test_rimuovi_dalla_coda_giorno_non_presente_non_fa_nulla(hass, make_ireti_coordinator):
    coordinator = make_ireti_coordinator(
        pods=[POD_A],
        data={CONF_GIORNI_DA_RIPROVARE: {POD_A: {"2026-09-10": "2026-09-10"}}},
    )

    with freeze_time(OGGI):
        coordinator._rimuovi_dalla_coda(POD_A, [date(2026, 1, 1)])
        code = coordinator._leggi_code()

    assert set(code[POD_A]) == {"2026-09-10"}


async def test_code_indipendenti_per_pod(hass, make_ireti_coordinator):
    coordinator = make_ireti_coordinator(
        pods=[POD_A, POD_B],
        data={CONF_GIORNI_DA_RIPROVARE: {POD_A: {"2026-09-10": "2026-09-10"}}},
    )

    with freeze_time(OGGI):
        coordinator._accoda_giorno(POD_B, date(2026, 9, 12))
        code = coordinator._leggi_code()

    assert set(code[POD_A]) == {"2026-09-10"}
    assert set(code[POD_B]) == {"2026-09-12"}


async def test_coda_oltre_il_massimo_scarta_i_piu_vecchi(hass, make_ireti_coordinator):
    oggi = date(2026, 9, 15)
    coda_iniziale = {
        (oggi - timedelta(days=i)).isoformat(): oggi.isoformat()
        for i in range(MAX_GIORNI_IN_CODA)
    }
    coordinator = make_ireti_coordinator(
        pods=[POD_A], data={CONF_GIORNI_DA_RIPROVARE: {POD_A: coda_iniziale}}
    )

    with freeze_time("2026-09-15 12:00:00"):
        # Un giorno in più fa scattare il taglio.
        coordinator._accoda_giorno(POD_A, oggi - timedelta(days=MAX_GIORNI_IN_CODA))
        code = coordinator._leggi_code()

    assert len(code[POD_A]) == MAX_GIORNI_IN_CODA
    # Il più vecchio (quello appena aggiunto) deve essere fuori.
    assert (oggi - timedelta(days=MAX_GIORNI_IN_CODA)).isoformat() not in code[POD_A]
