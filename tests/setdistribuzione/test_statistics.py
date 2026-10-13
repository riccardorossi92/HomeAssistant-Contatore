"""Test di distributors/setdistribuzione/statistics.py: dalle ore della
risposta reale ai bucket orari UTC. Solo funzioni pure: la scrittura vera
passa da statistics_common, coperta altrove.

Fuso Europe/Rome, come il portale: i giorni di cambio ora non sono mai
stati osservati, i test fissano il comportamento scelto (vedi docstring
del modulo).
"""
from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.contatore_letture.distributors.setdistribuzione import statistics as st

from .dati_reali import CONSUMI_GIORNO_12_10_2026

ORE_REALI = CONSUMI_GIORNO_12_10_2026["data"]["consumptions"]


@pytest.fixture(autouse=True)
def _fuso_roma():
    originale = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(ZoneInfo("Europe/Rome"))
    yield
    dt_util.set_default_time_zone(originale)


def _ore(giorno: date, n: int, kwh: float = 0.1) -> list[dict]:
    return [
        {"year": giorno.year, "month": giorno.month, "day": giorno.day, "hour": min(h, 23),
         "quarters": [kwh / 4] * 4, "total": kwh, "estimated": False}
        for h in range(n)
    ]


def test_giorno_reale_completo():
    ore = st.ore_del_giorno(ORE_REALI, date(2026, 10, 12))
    assert len(ore) == 24
    # Mezzanotte del 12/10 a Roma (ora legale) = 22:00 UTC dell'11.
    assert min(ore) == datetime(2026, 10, 11, 22, tzinfo=UTC)
    assert ore[datetime(2026, 10, 12, 11, tzinfo=UTC)] == pytest.approx(0.88199997)
    # Il totale coincide con quello che il portale dichiara per il giorno.
    assert sum(ore.values()) == pytest.approx(CONSUMI_GIORNO_12_10_2026["data"]["total"], abs=1e-4)


def test_ordine_delle_ore_non_conta():
    ore = st.ore_del_giorno(list(reversed(ORE_REALI)), date(2026, 10, 12))
    assert ore == st.ore_del_giorno(ORE_REALI, date(2026, 10, 12))


def test_ora_senza_total_usa_i_quarters():
    elementi = copy.deepcopy(ORE_REALI)
    del elementi[13]["total"]
    ore = st.ore_del_giorno(elementi, date(2026, 10, 12))
    assert ore[datetime(2026, 10, 12, 11, tzinfo=UTC)] == pytest.approx(0.882)


def test_giorno_incompleto_scartato():
    assert st.ore_del_giorno(ORE_REALI[:20], date(2026, 10, 12)) is None


def test_giorno_diverso_da_quello_richiesto():
    assert st.ore_del_giorno(ORE_REALI, date(2026, 10, 13)) is None


def test_risposta_vuota():
    assert st.ore_del_giorno([], date(2026, 10, 12)) is None


@pytest.mark.parametrize(("giorno", "attese"), [
    (date(2026, 10, 12), 24),
    (date(2026, 3, 29), 23),
    (date(2026, 10, 25), 25),
])
def test_ore_attese(giorno, attese):
    assert st.ore_attese(giorno) == attese


def test_giorno_da_25_ore_in_tempo_trascorso():
    giorno = date(2026, 10, 25)
    ore = st.ore_del_giorno(_ore(giorno, 25), giorno)
    assert len(ore) == 25
    assert min(ore) == datetime(2026, 10, 24, 22, tzinfo=UTC)
    assert max(ore) == datetime(2026, 10, 25, 22, tzinfo=UTC)


def test_giorno_da_23_ore():
    giorno = date(2026, 3, 29)
    ore = st.ore_del_giorno(_ore(giorno, 23), giorno)
    assert len(ore) == 23
    assert max(ore) == datetime(2026, 3, 29, 21, tzinfo=UTC)


def test_24_ore_nel_giorno_da_25_scartato():
    giorno = date(2026, 10, 25)
    assert st.ore_del_giorno(_ore(giorno, 24), giorno) is None
