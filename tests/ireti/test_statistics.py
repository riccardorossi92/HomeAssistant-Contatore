"""Test della logica di aggregazione oraria (distributors/ireti/statistics.py).

Coperte solo le funzioni pure (sanitize_statistic_id, _aggrega_per_ora): il percorso completo async_import_curva_giorni
richiede il recorder ed è fuori da questi test (stesso approccio di
tests/pcf_common/test_statistics.py).

Il fuso di riferimento è UTC (locale == UTC) per l'aggregazione in sé; i
giorni di cambio ora usano Europe/Rome con la forma reale delle risposte
vista nella issue #10.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.contatore_letture.distributors.ireti import statistics as st


@pytest.fixture(autouse=True)
def _fuso_utc():
    """_aggrega_per_ora legge dt_util.DEFAULT_TIME_ZONE (stesso principio
    di areti/statistics.py) - senza fissarlo qui il test dipenderebbe dal
    fuso lasciato da qualunque altro test giri prima nella stessa sessione
    pytest (es. quello di default di pytest-homeassistant-custom-component,
    che non è UTC)."""
    originale = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(UTC)
    yield
    dt_util.set_default_time_zone(originale)


def _lp(giorno: str, sample_values: list, energy_type: str = "A1") -> dict:
    return {
        "loadProfileDate": f"{giorno} 00:00:00 +0000",
        "serialNumber": "serial-fantasia",
        "timeType": "",
        "energyType": energy_type,
        "measType": "0",
        "sampleValues": sample_values,
    }


def _giorno_completo(valore: float, n: int = 96) -> list:
    return [valore] * n


# --- sanitize_statistic_id --------------------------------------------------

def test_statistic_id_dal_pod():
    assert st.sanitize_statistic_id("IT020E00000001") == "contatore_letture:it020e00000001_energia"


def test_statistic_id_sostituisce_i_caratteri_strani():
    assert st.sanitize_statistic_id("IT-020/E 1") == "contatore_letture:it_020_e_1_energia"


# --- _aggrega_per_ora ---------------------------------------------------

def test_quattro_quarti_dora_nello_stesso_bucket():
    campioni = _giorno_completo(0.0)
    campioni[0:4] = [0.25, 0.25, 0.25, 0.25]
    risultato = dict(st._aggrega_per_ora([_lp("01/08/2026", campioni)]))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(1.0)


def test_somma_su_piu_ore_dello_stesso_giorno():
    campioni = _giorno_completo(0.0)
    campioni[0] = 0.1   # 00:00
    campioni[4] = 0.2   # 01:00
    risultato = dict(st._aggrega_per_ora([_lp("01/08/2026", campioni)]))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(0.1)
    assert risultato[datetime(2026, 8, 1, 1, 0, tzinfo=UTC)] == pytest.approx(0.2)


def test_piu_giorni_in_un_colpo_solo():
    """Il caso reale di una finestra scorrevole: measures-loadprofiles
    torna un loadProfiles per ogni giorno del range, non uno solo."""
    c1 = _giorno_completo(0.0)
    c1[0] = 0.5
    c2 = _giorno_completo(0.0)
    c2[0] = 0.7
    risultato = dict(st._aggrega_per_ora([_lp("01/08/2026", c1), _lp("02/08/2026", c2)]))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(0.5)
    assert risultato[datetime(2026, 8, 2, 0, 0, tzinfo=UTC)] == pytest.approx(0.7)


def test_energy_type_diverso_da_a1_viene_scartato():
    """Solo l'energia attiva (A1) è importata con fiducia - vedi il
    docstring del modulo sul rischio di sommare tipi eterogenei."""
    campioni = _giorno_completo(0.0)
    campioni[0] = 99.0
    risultato = st._aggrega_per_ora([_lp("01/08/2026", campioni, energy_type="A2")])
    assert risultato == []


def test_valori_none_vengono_ignorati():
    campioni = _giorno_completo(0.0)
    campioni[0] = 0.3
    campioni[1] = None
    risultato = dict(st._aggrega_per_ora([_lp("01/08/2026", campioni)]))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(0.3)


def test_numero_di_campioni_inatteso_scarta_il_giorno():
    """Schema cambiato rispetto a quello confermato (96, o 92 nel cambio
    ora): meglio scartare l'intero giorno che importare dati probabilmente
    disallineati."""
    risultato = st._aggrega_per_ora([_lp("01/08/2026", [0.1, 0.2, 0.3])])
    assert risultato == []


@pytest.fixture
def fuso_roma():
    dt_util.set_default_time_zone(ZoneInfo("Europe/Rome"))


def _ore_utc(risultato) -> dict:
    return dict(risultato)


def test_giorno_da_23_ore(fuso_roma):
    """29/03/2026 (issue #10): un solo elemento da 92 campioni. L'indice è
    tempo trascorso dalla mezzanotte: 23 bucket da 4 campioni, nessuna
    ora doppia alle 03:00 e nessun buco alle 23:00."""
    campioni = [float(i // 4) for i in range(92)]  # ora trascorsa n -> valore n
    ore = _ore_utc(st._aggrega_per_ora([_lp("29/03/2026", campioni)]))

    assert len(ore) == 23
    mezzanotte = datetime(2026, 3, 28, 23, 0, tzinfo=UTC)  # 00:00 CET
    assert min(ore) == mezzanotte
    assert ore[mezzanotte] == pytest.approx(0.0)
    # 03:00 CEST = 01:00Z: seconda ora trascorsa, un'ora sola
    assert ore[datetime(2026, 3, 29, 1, 0, tzinfo=UTC)] == pytest.approx(4 * 2.0)
    # 23:00 CEST = 21:00Z: ultima ora del giorno, presente
    assert ore[datetime(2026, 3, 29, 21, 0, tzinfo=UTC)] == pytest.approx(4 * 22.0)


def _elementi_giorno_da_25_ore() -> list[dict]:
    """26/10/2025 come nella issue #10: 12 campioni (00:00-02:59 CEST) e
    96 con i primi 8 a null (dalle 02:00 CET). Valore = ora trascorsa."""
    corto = [float(i // 4) for i in range(12)]
    lungo = [None] * 8 + [float(3 + (i - 8) // 4) for i in range(8, 96)]
    return [_lp("26/10/2025", corto), _lp("26/10/2025", lungo)]


@pytest.mark.parametrize("inverti", [False, True])
def test_giorno_da_25_ore_unisce_i_due_elementi(fuso_roma, inverti):
    """Il portale non garantisce l'ordine: il risultato non deve cambiare."""
    elementi = _elementi_giorno_da_25_ore()
    if inverti:
        elementi.reverse()
    ore = _ore_utc(st._aggrega_per_ora(elementi))

    assert len(ore) == 25
    inizio = datetime(2025, 10, 25, 22, 0, tzinfo=UTC)  # 00:00 CEST
    for n in range(25):
        assert ore[inizio + timedelta(hours=n)] == pytest.approx(4.0 * n), n


def test_giorno_da_25_ore_con_un_solo_elemento_e_scartato(fuso_roma):
    """Senza l'elemento da 12 i campioni non tornano (96 su 100): meglio
    scartare il giorno che importarlo spostato."""
    _, lungo = _elementi_giorno_da_25_ore()
    assert st._aggrega_per_ora([lungo]) == []


def test_elemento_r1_accanto_ad_a1_non_cambia_il_giorno():
    """Ogni giorno arriva anche un elemento R1 (reattiva, issue #10)."""
    risultato = st._aggrega_per_ora(
        [_lp("01/08/2026", _giorno_completo(0.1)), _lp("01/08/2026", _giorno_completo(5.0), "R1")]
    )
    assert len(risultato) == 24
    assert all(v == pytest.approx(0.4) for _, v in risultato)


def test_loadprofile_senza_data_valida_viene_scartato():
    elemento = _lp("data-non-valida", _giorno_completo(0.1))
    risultato = st._aggrega_per_ora([elemento])
    assert risultato == []
