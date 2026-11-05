"""Test di distributors/edyna/statistics.py: dalle 25 colonne del portale ai
bucket orari UTC. Solo funzioni pure. Fuso Europe/Rome.

Il giorno da 25 ore è provato su dati reali (colonna 25 = seconda 02-03); il
giorno da 23 ore NON è mai stato osservato: i test fissano il comportamento
scelto (vedi docstring del modulo)."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.contatore_letture.distributors.edyna import statistics as st


@pytest.fixture(autouse=True)
def _fuso_roma():
    originale = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(ZoneInfo("Europe/Rome"))
    yield
    dt_util.set_default_time_zone(originale)


def _riga(base=0.5, ora25=0.0):
    return [base] * 24 + [ora25]


class TestOreAttese:
    @pytest.mark.parametrize(
        ("giorno", "ore"),
        [(date(2026, 10, 1), 24), (date(2026, 10, 25), 25), (date(2026, 3, 29), 23)],
    )
    def test_ore(self, giorno, ore):
        assert st.ore_attese(giorno) == ore


class TestGiornoNormale:
    def test_24_ore_dalla_mezzanotte_locale(self):
        ore = st.ore_del_giorno(_riga(0.5), date(2026, 10, 1))
        assert len(ore) == 24
        # Mezzanotte di Roma (CEST) = 22:00 UTC del giorno prima.
        assert min(ore) == datetime(2026, 9, 30, 22, 0, tzinfo=UTC)
        assert max(ore) == datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
        assert sum(ore.values()) == pytest.approx(12.0)

    def test_ora_25_diversa_da_zero_in_un_giorno_normale_scarta(self):
        assert st.ore_del_giorno(_riga(0.5, ora25=0.3), date(2026, 10, 1)) is None

    def test_cella_mancante_scarta(self):
        riga = _riga()
        riga[7] = None
        assert st.ore_del_giorno(riga, date(2026, 10, 1)) is None

    def test_valore_negativo_scarta(self):
        riga = _riga()
        riga[7] = -0.1
        assert st.ore_del_giorno(riga, date(2026, 10, 1)) is None

    def test_numero_di_colonne_inatteso_scarta(self):
        assert st.ore_del_giorno([0.5] * 24, date(2026, 10, 1)) is None

    def test_giorno_tutto_a_zero_non_e_pubblicato(self):
        assert st.ore_del_giorno([0.0] * 25, date(2026, 10, 1)) is None

    def test_zero_isolato_e_valido(self):
        riga = _riga()
        riga[3] = 0.0
        assert len(st.ore_del_giorno(riga, date(2026, 10, 1))) == 24


class TestCambioOra:
    def test_25_ore_la_colonna_25_e_la_seconda_02_03(self):
        riga = [float(i) for i in range(1, 25)] + [99.0]  # colonna k vale k
        ore = st.ore_del_giorno(riga, date(2026, 10, 25))
        assert len(ore) == 25
        mezzanotte = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)

        def ora(k: int) -> float:
            return ore[mezzanotte + timedelta(hours=k)]

        # Ore trascorse 0-2: colonne 1-3.
        assert [ora(0), ora(1), ora(2)] == [1.0, 2.0, 3.0]
        # Ora trascorsa 3 (la seconda 02-03, CET): la colonna 25.
        assert ora(3) == 99.0
        # Dopo, le colonne 4-24 scorrono di una.
        assert ora(4) == 4.0 and ora(24) == 24.0

    def test_25_ore_con_ora_25_vuota_scarta(self):
        riga = _riga(0.5)
        riga[24] = None
        assert st.ore_del_giorno(riga, date(2026, 10, 25)) is None

    def test_23_ore_ora_3_a_zero(self):
        riga = [float(i) for i in range(1, 25)] + [0.0]
        riga[2] = 0.0
        ore = st.ore_del_giorno(riga, date(2026, 3, 29))
        assert len(ore) == 23
        assert sum(ore.values()) == pytest.approx(sum(range(1, 25)) - 3)

    def test_23_ore_con_ora_3_piena_scarta(self):
        assert st.ore_del_giorno(_riga(0.5), date(2026, 3, 29)) is None
