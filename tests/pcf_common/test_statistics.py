"""Test della logica di import statistiche PCF: aggregazione oraria e
gestione del cambio ora tramite il flag FL_ORA_LEGALE.

Coperte solo le funzioni pure (_sanitize_statistic_id, _timestamp_aware,
_aggrega_per_ora). Il percorso completo async_import_curva richiede il
recorder ed è fuori da questi test.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from custom_components.contatore_letture.distributors.pcf_common import statistics as st
from custom_components.contatore_letture.distributors.pcf_common.api import CurvaPunto


def _p(iso_local: str, kwh: float, flag: str | None) -> CurvaPunto:
    return CurvaPunto(timestamp=datetime.fromisoformat(iso_local), valore_kwh=kwh, ora_legale=flag)


# --- _sanitize_statistic_id -------------------------------------------------

def test_statistic_id_dal_pod():
    assert st._sanitize_statistic_id("IT001E00000001") == "contatore_letture:it001e00000001_energia"


def test_statistic_id_sostituisce_i_caratteri_strani():
    assert st._sanitize_statistic_id("IT-001/E 1") == "contatore_letture:it_001_e_1_energia"


# --- _timestamp_aware -----------------------------------------------------

def test_flag_1_e_ora_solare_utc_piu_1():
    ts = st._timestamp_aware(_p("2026-01-15T08:00:00", 1.0, "1"))
    assert ts.utcoffset().total_seconds() == 3600


def test_flag_2_e_ora_legale_utc_piu_2():
    ts = st._timestamp_aware(_p("2026-07-15T08:00:00", 1.0, "2"))
    assert ts.utcoffset().total_seconds() == 7200


def test_timestamp_gia_aware_resta_invariato():
    aware = datetime(2026, 1, 1, 12, tzinfo=UTC)
    punto = CurvaPunto(timestamp=aware, valore_kwh=1.0, ora_legale="1")
    assert st._timestamp_aware(punto) is aware


def test_flag_sconosciuto_ripiega_sul_fuso_locale_e_avvisa_una_volta(monkeypatch, caplog):
    monkeypatch.setattr(st, "_flag_sconosciuti_segnalati", set())
    punto = _p("2026-01-15T08:00:00", 1.0, "9")

    with caplog.at_level(logging.WARNING):
        primo = st._timestamp_aware(punto)
        st._timestamp_aware(punto)

    assert primo.tzinfo is not None
    assert sum("FL_ORA_LEGALE non riconosciuto" in r.message for r in caplog.records) == 1


# --- _aggrega_per_ora ---------------------------------------------------

def test_quattro_quarti_dora_nello_stesso_bucket():
    punti = [_p(f"2026-01-15T08:{m:02d}:00", 0.25, "1") for m in (0, 15, 30, 45)]
    risultato = st._aggrega_per_ora(punti)
    assert risultato == [(datetime(2026, 1, 15, 7, 0, tzinfo=UTC), 1.0)]


def test_cambio_ora_primavera_i_segnaposto_a_zero_non_raddoppiano():
    # 29/03/2026: le 02:00 locali non esistono. Le righe reali 01:00-01:45
    # (flag "1", CET) e i segnaposto 02:00-02:45 (flag "2", CEST, a zero)
    # cadono nello stesso bucket UTC 00:00.
    reali = [_p(f"2026-03-29T01:{m:02d}:00", 0.5, "1") for m in (0, 15, 30, 45)]
    segnaposto = [_p(f"2026-03-29T02:{m:02d}:00", 0.0, "2") for m in (0, 15, 30, 45)]
    risultato = st._aggrega_per_ora(reali + segnaposto)
    assert risultato == [(datetime(2026, 3, 29, 0, 0, tzinfo=UTC), 2.0)]


def test_cambio_ora_autunno_l_ora_ripetuta_resta_divisa_in_due_bucket():
    # 25/10/2026: le 02:00 locali si ripetono. Prima passata flag "2" (CEST),
    # seconda flag "1" (CET): istanti UTC diversi, due bucket distinti.
    prima = [_p(f"2026-10-25T02:{m:02d}:00", 0.1, "2") for m in (0, 15, 30, 45)]
    seconda = [_p(f"2026-10-25T02:{m:02d}:00", 0.2, "1") for m in (0, 15, 30, 45)]
    risultato = dict(st._aggrega_per_ora(prima + seconda))
    assert risultato == {
        datetime(2026, 10, 25, 0, 0, tzinfo=UTC): pytest.approx(0.4),
        datetime(2026, 10, 25, 1, 0, tzinfo=UTC): pytest.approx(0.8),
    }


def test_avvisa_se_un_bucket_ha_piu_di_quattro_intervalli_non_nulli(caplog):
    # 5 intervalli non nulli nella stessa ora UTC: non è più il caso
    # "segnaposto a zero", il totale potrebbe essere sovrastimato.
    punti = [_p(f"2026-01-15T08:{m:02d}:00", 0.1, "1") for m in (0, 15, 30, 45)]
    punti.append(_p("2026-01-15T08:00:00", 0.1, "1"))

    with caplog.at_level(logging.WARNING):
        st._aggrega_per_ora(punti)

    assert any("sovrastimato" in r.message for r in caplog.records)
