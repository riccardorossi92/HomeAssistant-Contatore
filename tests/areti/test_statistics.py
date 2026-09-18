"""Test della logica di aggregazione oraria (distributors/areti/statistics.py).

Coperte solo le funzioni pure (sanitize_statistic_id, _aggrega_per_ora): il
percorso completo async_import_curva_mensile richiede il recorder ed è
fuori da questi test (stesso approccio di tests/pcf_common/test_statistics.py
e tests/ireti/test_statistics.py).

Il fuso di riferimento è quello di default di Home Assistant nei test
(UTC), quindi qui locale == UTC: non serve simulare il cambio ora per
testare l'aggregazione in sé.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.util import dt as dt_util

from custom_components.contatore_letture.distributors.areti import statistics as st


@pytest.fixture(autouse=True)
def _fuso_utc():
    """_aggrega_per_ora legge dt_util.DEFAULT_TIME_ZONE - senza fissarlo
    qui il test dipenderebbe dal fuso lasciato da qualunque altro test giri
    prima nella stessa sessione pytest (es. quello di default di
    pytest-homeassistant-custom-component, che non è UTC)."""
    originale = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(UTC)
    yield
    dt_util.set_default_time_zone(originale)


def _el(giorno: str, ora: str, value) -> dict:
    return {"Value": value, "Ora": ora, "Data": giorno}


# --- sanitize_statistic_id --------------------------------------------------

def test_statistic_id_dal_pod():
    assert st.sanitize_statistic_id("IT001E00000001") == "contatore_letture:it001e00000001_energia"


def test_statistic_id_sostituisce_i_caratteri_strani():
    assert st.sanitize_statistic_id("IT-001/E 1") == "contatore_letture:it_001_e_1_energia"


# --- _aggrega_per_ora ---------------------------------------------------

def test_quattro_quarti_dora_nello_stesso_bucket():
    elementi = [
        _el("2026-08-01", "00:00:00", "0.25"),
        _el("2026-08-01", "00:15:00", "0.25"),
        _el("2026-08-01", "00:30:00", "0.25"),
        _el("2026-08-01", "00:45:00", "0.25"),
    ]
    risultato = dict(st._aggrega_per_ora(elementi))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(1.0)


def test_somma_su_piu_ore_dello_stesso_giorno():
    elementi = [
        _el("2026-08-01", "00:00:00", "0.1"),
        _el("2026-08-01", "01:00:00", "0.2"),
    ]
    risultato = dict(st._aggrega_per_ora(elementi))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(0.1)
    assert risultato[datetime(2026, 8, 1, 1, 0, tzinfo=UTC)] == pytest.approx(0.2)


def test_piu_giorni_in_un_colpo_solo():
    """Il caso reale di un mese intero: async_get_misurazioni torna un
    elemento per ogni quarto d'ora dell'intero mese, non di un solo giorno."""
    elementi = [
        _el("2026-08-01", "00:00:00", "0.5"),
        _el("2026-08-02", "00:00:00", "0.7"),
    ]
    risultato = dict(st._aggrega_per_ora(elementi))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(0.5)
    assert risultato[datetime(2026, 8, 2, 0, 0, tzinfo=UTC)] == pytest.approx(0.7)


def test_elemento_con_value_non_numerico_viene_scartato():
    elementi = [
        _el("2026-08-01", "00:00:00", "non-un-numero"),
        _el("2026-08-01", "00:15:00", "0.3"),
    ]
    risultato = dict(st._aggrega_per_ora(elementi))
    assert risultato[datetime(2026, 8, 1, 0, 0, tzinfo=UTC)] == pytest.approx(0.3)


def test_elemento_con_data_non_valida_viene_scartato():
    elementi = [_el("data-non-valida", "00:00:00", "0.3")]
    assert st._aggrega_per_ora(elementi) == []


def test_elemento_con_campo_mancante_viene_scartato():
    elemento = {"Value": "0.3", "Ora": "00:00:00"}  # manca "Data"
    assert st._aggrega_per_ora([elemento]) == []


def test_lista_vuota_produce_nessun_bucket():
    assert st._aggrega_per_ora([]) == []
