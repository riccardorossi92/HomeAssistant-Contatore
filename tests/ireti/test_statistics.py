"""Test della logica di aggregazione oraria (distributors/ireti/statistics.py).

Coperte solo le funzioni pure (sanitize_statistic_id, _ora_da_indice,
_aggrega_per_ora): il percorso completo async_import_curva_giorni
richiede il recorder ed è fuori da questi test (stesso approccio di
tests/pcf_common/test_statistics.py).

Il fuso di riferimento è quello di default di Home Assistant nei test
(UTC), quindi qui locale == UTC: non serve simulare il cambio ora per
testare l'aggregazione in sé, solo per il caso "92 campioni" (vedi sotto).
"""
from __future__ import annotations

from datetime import UTC, datetime, time

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


# --- _ora_da_indice ----------------------------------------------------------

def test_indice_zero_e_mezzanotte():
    assert st._ora_da_indice(0) == time(0, 0)


def test_indice_due_e_le_00_30():
    assert st._ora_da_indice(2) == time(0, 30)


def test_ultimo_indice_e_le_23_45():
    assert st._ora_da_indice(95) == time(23, 45)


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


def test_novantadue_campioni_e_accettato():
    """Giorno di cambio ora legale (ipotizzato dal bundle JS, non ancora
    osservato con dati reali) - non deve essere scartato solo perché ha
    92 campioni invece di 96."""
    campioni = _giorno_completo(0.1, n=92)
    risultato = st._aggrega_per_ora([_lp("29/03/2026", campioni)])
    assert len(risultato) > 0


def test_loadprofile_senza_data_valida_viene_scartato():
    elemento = _lp("data-non-valida", _giorno_completo(0.1))
    risultato = st._aggrega_per_ora([elemento])
    assert risultato == []
