"""Test di distributors/edyna/parse.py sul markup reale (anonimizzato) della
cattura del 05/11/2026. Funzioni pure, nessuna rete."""
from __future__ import annotations

from datetime import date

import pytest

from custom_components.contatore_letture.distributors.edyna import parse as p

from . import dati_reali as d


def _soup(html):
    return p.parse_html(html)


class TestForm:
    def test_campi_come_il_browser(self):
        campi = p.campi_form(_soup(d.pagina_curve()))
        nomi = [n for n, _ in campi]
        assert ("__VIEWSTATE", "VS-3") in campi
        assert ("__EVENTVALIDATION", "EV-3") in campi
        # Il select porta il valore selezionato, non tutte le opzioni.
        anno = [v for n, v in campi if n.endswith("ddlAnno")]
        assert anno == ["2026"]
        # I pulsanti submit non si inviano.
        assert "ctl00$btnDefault" not in nomi

    def test_checkbox_e_radio_solo_se_spuntati(self):
        html = d.pagina(
            '<input type="radio" name="r" value="A" /><input type="radio" name="r" value="B" checked />'
            '<input type="checkbox" name="c" /><input type="checkbox" name="c2" value="x" checked />'
            '<input type="text" name="t" disabled value="no" /><input type="text" name="u" value="si" />'
        )
        campi = dict(p.campi_form(_soup(html)))
        assert campi["r"] == "B"
        assert "c" not in campi
        assert campi["c2"] == "x"
        assert "t" not in campi
        assert campi["u"] == "si"

    def test_select_senza_selezione_usa_la_prima_opzione(self):
        html = d.pagina(
            '<select name="s"><option value="">tutti</option><option value="E">E</option></select>'
        )
        assert dict(p.campi_form(_soup(html)))["s"] == ""

    def test_senza_form_solleva(self):
        with pytest.raises(p.EdynaParseError):
            p.campi_form(_soup("<html><body>niente</body></html>"))

    def test_dati_postback_imposta_target_e_sovrascrive(self):
        campi = [("__EVENTTARGET", ""), ("__EVENTARGUMENT", ""), ("a", "1"), ("sel", "2026")]
        dati = p.dati_postback(campi, "ctl00$x", sovrascrivi={"sel": "2025", "nuovo": "v"})
        assert dati == [
            ("__EVENTTARGET", "ctl00$x"),
            ("__EVENTARGUMENT", ""),
            ("a", "1"),
            ("sel", "2025"),
            ("nuovo", "v"),
        ]

    def test_dati_postback_aggiunge_target_se_manca(self):
        dati = p.dati_postback([("a", "1")], "t")
        assert ("__EVENTTARGET", "t") in dati and ("__EVENTARGUMENT", "") in dati


class TestLink:
    def test_target_da_href(self):
        assert p.target_da_href("javascript:__doPostBack('a$b','')") == "a$b"
        assert p.target_da_href("http://x") is None
        assert p.target_da_href(None) is None

    def test_voce_di_menu(self):
        s = _soup(d.pagina_home())
        assert p.target_menu(s, "Utenze").endswith("FirstLevelMenuRepeater$ctl01$lnkLevelMenu")
        assert p.target_menu(s, "Non esiste") is None

    def test_pagina_di_login(self):
        assert p.e_pagina_di_login(_soup(d.MODULO_LOGIN))
        assert not p.e_pagina_di_login(_soup(d.pagina_home()))


class TestDelta:
    def test_redirect_dal_login(self):
        assert p.redirect_dal_delta(d.RISPOSTA_LOGIN_OK) == "/EIPPUF/EIPPUF/it-IT/dD4/Page/Single.tws"

    def test_nessun_redirect(self):
        assert p.redirect_dal_delta(d.RISPOSTA_LOGIN_RIFIUTATA) is None

    @pytest.mark.parametrize("testo", ["", "abc", "5|x|y", "x|a|b|c|"])
    def test_testo_malformato_non_solleva(self, testo):
        assert p.redirect_dal_delta(testo) is None


class TestUtenze:
    def test_una_fornitura(self):
        f = p.parse_utenze(_soup(d.pagina_utenze()))
        assert len(f) == 1
        assert (f[0].pod, f[0].servizio, f[0].ruolo, f[0].stato) == (
            d.POD, "Energia Elettrica", "Prelievo", "Attivo",
        )
        assert f[0].target_curve.endswith("gvUtenze$ctl02$btnCurve")

    def test_piu_righe_e_target_per_riga(self):
        f = p.parse_utenze(_soup(d.pagina_utenze(d.GRIGLIA_UTENZE_MULTI)))
        assert [(x.pod, x.ruolo, x.servizio) for x in f] == [
            (d.POD, "Prelievo", "Energia Elettrica"),
            (d.POD_IMMISSIONE, "Immissione", "Energia Elettrica"),
            ("00000000000001", "Prelievo", "Gas"),
            (d.POD_SECONDO, "Prelievo", "Energia Elettrica"),
        ]
        assert [x.target_curve.split("$")[-2] for x in f] == ["ctl02", "ctl03", "ctl04", "ctl05"]

    def test_account_senza_pod(self):
        assert p.parse_utenze(_soup(d.pagina_utenze_vuota())) == []

    def test_intestazioni_inattese(self):
        html = d.pagina('<table id="x_gvUtenze"><tr><th>Altro</th></tr></table>')
        with pytest.raises(p.EdynaParseError):
            p.parse_utenze(_soup(html))


class TestCurveMesi:
    def test_anno(self):
        s = _soup(d.pagina_curve())
        assert p.anno_selezionato(s) == 2026
        assert p.anni_disponibili(s) == [2026, 2025, 2024, 2023, 2022, 2021, 2020]
        assert p.nome_campo_anno(s).endswith("TCurve$cCurve$ddlAnno")

    def test_altro_anno_selezionato(self):
        s = _soup(d.pagina_curve(selettore=d.selettore_anno(2025)))
        assert p.anno_selezionato(s) == 2025

    def test_target_e_valore_del_mese(self):
        s = _soup(d.pagina_curve())
        assert p.target_mese(s, 10).endswith("gvCurveAttiva$ctl02$btnCurve10")
        # btnCurve1 non deve confondersi con btnCurve10/11/12.
        assert p.target_mese(s, 1).endswith("gvCurveAttiva$ctl02$btnCurve1")
        assert p.kwh_mese(s, 10) == pytest.approx(200.5)
        assert p.kwh_mese(s, 11) is None  # link senza valore: mese senza dati

    def test_tabella_dei_mesi_vuota(self):
        # "Nessun risultato": quello che il portale mostra alla prima richiesta.
        vuota = _soup(d.pagina_curve(tabella=d.TABELLA_MESI_VUOTA))
        assert p.tabella_mesi_vuota(vuota)
        assert p.target_mese(vuota, 10) is None and p.kwh_mese(vuota, 10) is None
        assert p.anno_selezionato(vuota) == 2026  # il selettore c'è già
        assert not p.tabella_mesi_vuota(_soup(d.pagina_curve()))

    def test_senza_tab_curve(self):
        s = _soup(d.pagina_utenze())
        assert p.anno_selezionato(s) is None and p.anni_disponibili(s) == []
        assert p.target_mese(s, 10) is None


class TestCurvaOraria:
    def test_titolo_e_pod(self):
        s = _soup(d.pagina_curve_orarie())
        assert p.mese_della_curva(s) == (2026, 10)
        assert p.pod_della_curva(s) == d.POD

    def test_righe(self):
        c = p.parse_curva_oraria(_soup(d.pagina_curve_orarie()))
        assert list(c) == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 24), date(2026, 10, 25)]
        assert all(len(v) == 25 for v in c.values())
        assert c[date(2026, 10, 1)][0] == pytest.approx(0.2)
        assert c[date(2026, 10, 1)][24] == 0.0
        assert c[date(2026, 10, 25)][24] == pytest.approx(0.77)

    def test_cella_vuota_diventa_none(self):
        c = p.parse_curva_oraria(_soup(d.pagina_curve_orarie(d.GRIGLIA_ORARIA_ANOMALA)))
        assert c[date(2026, 10, 3)][5] is None

    def test_senza_griglia_solleva(self):
        with pytest.raises(p.EdynaParseError):
            p.parse_curva_oraria(_soup(d.pagina_curve()))

    @pytest.mark.parametrize(
        ("testo", "atteso"),
        [("0,176", 0.176), ("1.234,5", 1234.5), (" 2 ", 2.0), ("", None), ("n.d.", None)],
    )
    def test_numero_italiano(self, testo, atteso):
        assert p.numero_italiano(testo) == atteso
