"""Test per la logica di date e pianificazione in coordinator.py.

A differenza di test_api.py, questi richiedono Home Assistant installato
(coordinator.py importa homeassistant.*), quindi vanno eseguiti in un
ambiente con pytest-homeassistant-custom-component (vedi requirements_test.txt).
"""
from datetime import date, timedelta

import pytest

from custom_components.contatore_letture.distributors.pcf_common.const import (
    MAX_DATE_RANGE_MONTHS,
    MAX_GIORNI_IN_CODA,
    MAX_TENTATIVI_PER_GIORNO,
    ORA_MINIMA_RICHIESTA,
    RITARDO_DATI_GIORNI,
)
from custom_components.contatore_letture.distributors.pcf_common.coordinator import (
    _giorni_nel_periodo,
    _inizio_n_mesi_prima,
    _mese_precedente_completo,
)


class TestInizioNMesiPrima:
    """_inizio_n_mesi_prima calcola l'inizio di un blocco di N mesi che
    termina in una data data. È il cuore del recupero storico a ritroso."""

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
        """Il manuale Duereti impone un range massimo di 6 mesi per
        requestExport: verifichiamo di restare al limite, mai oltre."""
        fine = date(2026, 8, 1)
        inizio = _inizio_n_mesi_prima(fine, MAX_DATE_RANGE_MONTHS)
        mesi_di_range = (fine.year - inizio.year) * 12 + (fine.month - inizio.month)
        assert mesi_di_range <= MAX_DATE_RANGE_MONTHS


class TestMesePrecedenteCompleto:
    """Usata solo come default per l'azione recupera_ticket quando l'utente
    non indica le date."""

    def test_caso_normale(self):
        assert _mese_precedente_completo(date(2026, 8, 2)) == (
            date(2026, 7, 1),
            date(2026, 7, 31),
        )

    def test_cambio_anno(self):
        assert _mese_precedente_completo(date(2026, 1, 15)) == (
            date(2025, 12, 1),
            date(2025, 12, 31),
        )

    def test_febbraio_bisestile(self):
        assert _mese_precedente_completo(date(2028, 3, 1)) == (
            date(2028, 2, 1),
            date(2028, 2, 29),
        )


class TestRitardoDati:
    """Il ritardo con cui Duereti rende disponibili i dati, verificato sul
    campo: la richiesta del 3 agosto, inviata la mattina del 4, è stata evasa
    alle 15 dello stesso 4 agosto."""

    def test_il_ritardo_e_di_un_giorno(self):
        assert RITARDO_DATI_GIORNI == 1

    def test_giorno_richiesto_a_regime(self):
        oggi = date(2026, 8, 4)
        assert oggi - timedelta(days=RITARDO_DATI_GIORNI) == date(2026, 8, 3)

    def test_ora_di_partenza_e_la_sera(self):
        """Verificato sul campo che di mattina i dati del giorno precedente
        spesso non sono ancora pubblicati, mentre alle 19 sì. È solo il valore
        di partenza: l'utente può cambiarlo dalle opzioni."""
        assert ORA_MINIMA_RICHIESTA == 19


class TestGiorniNelPeriodo:
    """Serve a sapere quali giorni una richiesta copriva, per confrontarli con
    quelli effettivamente presenti nel file ricevuto."""

    def test_un_solo_giorno(self):
        assert _giorni_nel_periodo(date(2026, 8, 15), date(2026, 8, 15)) == [
            date(2026, 8, 15)
        ]

    def test_estremi_inclusi(self):
        giorni = _giorni_nel_periodo(date(2026, 8, 14), date(2026, 8, 16))
        assert giorni == [date(2026, 8, 14), date(2026, 8, 15), date(2026, 8, 16)]

    def test_attraversa_il_cambio_di_mese(self):
        giorni = _giorni_nel_periodo(date(2026, 7, 30), date(2026, 8, 2))
        assert len(giorni) == 4
        assert giorni[0] == date(2026, 7, 30) and giorni[-1] == date(2026, 8, 2)

    def test_periodo_invertito_e_vuoto(self):
        """Non solleva: un periodo con fine prima dell'inizio non contiene
        giorni. La validazione delle date sta a monte, in recupera_storico."""
        assert _giorni_nel_periodo(date(2026, 8, 16), date(2026, 8, 14)) == []


class TestCodaGiorniDaRiprovare:
    """La coda dei giorni che Duereti non ha ancora prodotto. Qui si verifica
    la logica di ordinamento e di scarto, indipendentemente dalla persistenza
    sulla config entry (che richiederebbe l'ambiente di Home Assistant).
    """

    @staticmethod
    def _prossimo_arretrato(coda: dict, atteso: date):
        """Replica la scelta fatta da _prossima_richiesta: gli arretrati hanno
        la precedenza sul giorno atteso, e si parte dal più vecchio."""
        arretrati = sorted(g for g in coda if date.fromisoformat(g) < atteso)
        return date.fromisoformat(arretrati[0]) if arretrati else atteso

    def test_gli_arretrati_hanno_la_precedenza(self):
        coda = {"2026-08-10": 1}
        atteso = date(2026, 8, 15)
        assert self._prossimo_arretrato(coda, atteso) == date(2026, 8, 10)

    def test_si_parte_dal_piu_vecchio(self):
        coda = {"2026-08-12": 3, "2026-08-10": 1, "2026-08-11": 2}
        assert self._prossimo_arretrato(coda, date(2026, 8, 15)) == date(2026, 8, 10)

    def test_senza_arretrati_si_chiede_il_giorno_atteso(self):
        atteso = date(2026, 8, 15)
        assert self._prossimo_arretrato({}, atteso) == atteso

    def test_il_giorno_atteso_non_e_un_arretrato_di_se_stesso(self):
        """Se il giorno atteso è già in coda non deve essere trattato come
        arretrato, altrimenti si creerebbe un ciclo."""
        atteso = date(2026, 8, 15)
        coda = {"2026-08-15": 2}
        assert self._prossimo_arretrato(coda, atteso) == atteso

    def test_un_giorno_viene_abbandonato_dopo_il_limite(self):
        coda = {"2026-01-01": MAX_TENTATIVI_PER_GIORNO - 1}
        coda["2026-01-01"] += 1
        rimasti = {g: t for g, t in coda.items() if t < MAX_TENTATIVI_PER_GIORNO}
        assert rimasti == {}

    def test_la_coda_e_limitata_ai_giorni_piu_recenti(self):
        coda = {
            (date(2026, 1, 1) + timedelta(days=i)).isoformat(): 1
            for i in range(MAX_GIORNI_IN_CODA + 5)
        }
        tenuti = sorted(coda, reverse=True)[:MAX_GIORNI_IN_CODA]
        assert len(tenuti) == MAX_GIORNI_IN_CODA
        # devono restare i più recenti, non i primi inseriti
        assert max(tenuti) == max(coda)
        assert min(tenuti) > min(coda)


class TestOraRichiestaDalleOpzioni:
    """Lettura dell'ora configurata dall'utente.

    Il NumberSelector di Home Assistant restituisce un float, e le opzioni
    salvate da versioni diverse possono contenere tipi diversi: la lettura
    deve essere tollerante, perché ignorare in silenzio la scelta dell'utente
    sarebbe peggio che convertirla. Solo un valore davvero inutilizzabile
    ricade sul default.
    """

    @staticmethod
    def _leggi(valore):
        """Replica la logica di DuretiCoordinator._ora_richiesta."""
        if valore is None:
            return ORA_MINIMA_RICHIESTA
        try:
            ora = int(float(valore))
        except (TypeError, ValueError):
            return ORA_MINIMA_RICHIESTA
        return ora if 0 <= ora <= 23 else ORA_MINIMA_RICHIESTA

    def test_opzione_assente_usa_il_default(self):
        assert self._leggi(None) == ORA_MINIMA_RICHIESTA

    def test_intero(self):
        assert self._leggi(12) == 12

    def test_float_dal_number_selector(self):
        """Il selettore restituisce float: 12.0 deve valere come 12."""
        assert self._leggi(12.0) == 12

    def test_stringa(self):
        assert self._leggi("12") == 12

    def test_estremi_ammessi(self):
        assert self._leggi(0) == 0
        assert self._leggi(23) == 23

    @pytest.mark.parametrize("valore", [24, 25, -1, 100])
    def test_fuori_intervallo_usa_il_default(self, valore):
        assert self._leggi(valore) == ORA_MINIMA_RICHIESTA

    @pytest.mark.parametrize("valore", ["abc", "", [], {}])
    def test_valore_non_numerico_usa_il_default(self, valore):
        assert self._leggi(valore) == ORA_MINIMA_RICHIESTA
