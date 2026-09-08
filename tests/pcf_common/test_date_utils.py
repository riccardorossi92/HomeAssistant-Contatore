"""Test per pcf_common/date_utils.py: helper di mese puri.

Non dipendono da Home Assistant (date_utils.py importa solo la libreria
standard), quindi girano con un semplice `pytest tests/pcf_common/test_date_utils.py`.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parent.parent.parent / "custom_components" / "contatore_letture" / "distributors"),
)

from pcf_common.date_utils import (  # noqa: E402
    mese_e_chiuso,
    mese_precedente,
    mese_precedente_completo,
    mese_str,
    mese_successivo,
    primo_giorno_mese,
    ultimo_giorno_mese,
    ultimo_mese_chiuso,
)


class TestFormatoMese:
    def test_mese_str(self):
        assert mese_str(date(2026, 9, 8)) == "2026-09"
        assert mese_str(date(2026, 12, 31)) == "2026-12"

    def test_primo_e_ultimo_giorno(self):
        assert primo_giorno_mese("2026-02") == date(2026, 2, 1)
        assert ultimo_giorno_mese("2026-02") == date(2026, 2, 28)
        assert ultimo_giorno_mese("2028-02") == date(2028, 2, 29)  # bisestile
        assert ultimo_giorno_mese("2026-12") == date(2026, 12, 31)


class TestMeseSuccessivoEPrecedente:
    def test_successivo(self):
        assert mese_successivo("2026-08") == "2026-09"
        assert mese_successivo("2026-12") == "2027-01"

    def test_precedente(self):
        assert mese_precedente("2026-09") == "2026-08"
        assert mese_precedente("2026-01") == "2025-12"

    def test_andata_e_ritorno(self):
        for m in ("2026-01", "2026-07", "2026-12"):
            assert mese_precedente(mese_successivo(m)) == m


class TestMeseChiuso:
    """Una CURVA è richiedibile solo per un mese solare già concluso
    (manuale PCF, 08/09/2026: non "relative al mese corrente")."""

    def test_mese_scorso_e_chiuso(self):
        assert mese_e_chiuso("2026-08", date(2026, 9, 8)) is True

    def test_mese_corrente_non_e_chiuso(self):
        assert mese_e_chiuso("2026-09", date(2026, 9, 8)) is False

    def test_mese_futuro_non_e_chiuso(self):
        assert mese_e_chiuso("2026-10", date(2026, 9, 8)) is False

    def test_primo_del_mese(self):
        # Il 1° settembre agosto è appena chiuso, settembre no.
        assert mese_e_chiuso("2026-08", date(2026, 9, 1)) is True
        assert mese_e_chiuso("2026-09", date(2026, 9, 1)) is False


class TestUltimoMeseChiuso:
    def test_caso_normale(self):
        assert ultimo_mese_chiuso(date(2026, 9, 8)) == "2026-08"

    def test_cambio_anno(self):
        assert ultimo_mese_chiuso(date(2026, 1, 15)) == "2025-12"


class TestMesePrecedenteCompleto:
    """Default per l'azione recupera_ticket senza date, e giorno di prova
    per la verifica del POD in configurazione (async_valida_pod)."""

    def test_caso_normale(self):
        assert mese_precedente_completo(date(2026, 8, 2)) == (
            date(2026, 7, 1),
            date(2026, 7, 31),
        )

    def test_cambio_anno(self):
        assert mese_precedente_completo(date(2026, 1, 15)) == (
            date(2025, 12, 1),
            date(2025, 12, 31),
        )

    def test_febbraio_bisestile(self):
        assert mese_precedente_completo(date(2028, 3, 1)) == (
            date(2028, 2, 1),
            date(2028, 2, 29),
        )

    @pytest.mark.parametrize("giorno", [date(2026, 9, 1), date(2026, 9, 15), date(2026, 9, 30)])
    def test_e_sempre_un_mese_gia_chiuso(self, giorno):
        data_da, data_a = mese_precedente_completo(giorno)
        assert mese_e_chiuso(mese_str(data_da), giorno)
        assert data_a < giorno.replace(day=1)
