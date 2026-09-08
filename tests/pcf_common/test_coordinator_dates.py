"""Test per gli helper di date locali a coordinator.py.

A differenza di test_api.py / test_date_utils.py, questi importano
coordinator.py (che importa homeassistant.*), quindi vanno eseguiti in un
ambiente con pytest-homeassistant-custom-component (vedi requirements_test.txt).
Gli helper puri di mese (formato "YYYY-MM", mese chiuso, mese precedente
completo) sono in test_date_utils.py, che non richiede Home Assistant.
"""
from datetime import date, timedelta

import pytest

from custom_components.contatore_letture.distributors.pcf_common.const import (
    MAX_DATE_RANGE_MONTHS,
)
from custom_components.contatore_letture.distributors.pcf_common.coordinator import (
    _inizio_n_mesi_prima,
)


class TestInizioNMesiPrima:
    """_inizio_n_mesi_prima calcola l'inizio di un blocco di N mesi che
    termina in una data data. È il cuore della validazione del recupero
    storico (limite di 6 mesi per richiesta imposto dalle API PCF)."""

    @pytest.mark.parametrize("n_mesi", range(1, MAX_DATE_RANGE_MONTHS + 1))
    def test_copre_esattamente_n_mesi(self, n_mesi):
        fine = date(2026, 7, 31)
        inizio = _inizio_n_mesi_prima(fine, n_mesi)
        mesi_coperti = (fine.year - inizio.year) * 12 + (fine.month - inizio.month) + 1
        assert mesi_coperti == n_mesi
        assert inizio.day == 1

    def test_un_mese_coincide_col_mese_di_fine(self):
        assert _inizio_n_mesi_prima(date(2026, 7, 31), 1) == date(2026, 7, 1)

    def test_attraversa_il_cambio_di_anno(self):
        # 6 mesi che finiscono a fine febbraio 2026 partono da settembre 2025
        assert _inizio_n_mesi_prima(date(2026, 2, 28), 6) == date(2025, 9, 1)

    def test_blocchi_consecutivi_non_lasciano_buchi(self):
        """Simula il recupero storico: ogni blocco deve iniziare il giorno
        dopo la fine del blocco precedente, senza sovrapposizioni né buchi."""
        fine = date(2026, 7, 31)
        confine_precedente = None
        for _ in range(4):
            inizio = _inizio_n_mesi_prima(fine, MAX_DATE_RANGE_MONTHS)
            if confine_precedente is not None:
                assert fine + timedelta(days=1) == confine_precedente
            confine_precedente = inizio
            fine = inizio - timedelta(days=1)

    def test_non_supera_mai_il_limite_dell_api(self):
        """Il manuale PCF impone un range massimo di 6 mesi per
        requestExport: verifichiamo di restare al limite, mai oltre."""
        fine = date(2026, 8, 1)
        inizio = _inizio_n_mesi_prima(fine, MAX_DATE_RANGE_MONTHS)
        mesi_di_range = (fine.year - inizio.year) * 12 + (fine.month - inizio.month)
        assert mesi_di_range <= MAX_DATE_RANGE_MONTHS
