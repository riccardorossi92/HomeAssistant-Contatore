"""Test per pcf_common/api.py: parsing dei file CSV/XLSX restituiti dal
distributore (formato PCF, comune a Duereti/Unareti).

Questi test NON dipendono da Home Assistant (api.py importa solo aiohttp e
libreria standard), quindi girano con un semplice `pytest tests/test_api.py`.

Ereditati (con adattamento import/nomi classe) da HomeAssistant-Duereti:
la logica di parsing è invariata.
"""
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "contatore_letture" / "distributors"))

from pcf_common.api import (  # noqa: E402
    PcfApiError,
    PcfAuthError,
    parse_curve_zip,
    parse_letture_zip,
)

CSV_ESEMPIO = (
    "POD;DATA;ORA;FL_ORA_LEGALE;ATTIVA_PRELEVATA;ATTIVA_IMMESSA;"
    "REATTIVA_CAPACITIVA_IMMESSA;REATTIVA_CAPACITIVA_PRELEVATA;"
    "REATTIVA_INDUTTIVA_IMMESSA;REATTIVA_INDUTTIVA_PRELEVATA;"
    "PICCO_PRELEVATA;CONSUMO_PICCO_IMMESSA;TIPO_DATO;\n"
    "IT001E00000001;20260501;000000;2;0,025;;;;;0,001;5,004;;E;\n"
    "IT001E00000001;20260501;001500;2;0,028;;;;;0,0;5,004;;E;\n"
    "IT001E00000001;20260501;003000;2;0,037;;;;;0,0;5,004;;E;\n"
)


def _zip_da_csv(nome_file: str, contenuto: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(nome_file, contenuto)
    return buf.getvalue()


def test_parse_curve_zip_righe_valide():
    zip_bytes = _zip_da_csv("C_test_IT001E00000001.csv", CSV_ESEMPIO)
    risultati = parse_curve_zip(zip_bytes)

    assert "IT001E00000001" in risultati
    punti = risultati["IT001E00000001"].punti
    assert len(punti) == 3
    assert punti[0].valore_kwh == pytest.approx(0.025)
    assert punti[0].timestamp.hour == 0
    assert punti[0].timestamp.minute == 0
    assert punti[1].timestamp.minute == 15


def test_parse_curve_zip_decimali_con_virgola():
    zip_bytes = _zip_da_csv("C_test.csv", CSV_ESEMPIO)
    risultati = parse_curve_zip(zip_bytes)
    punti = risultati["IT001E00000001"].punti
    # "0,037" deve diventare 0.037, non essere scartato o interpretato male
    assert any(p.valore_kwh == pytest.approx(0.037) for p in punti)


def test_parse_curve_zip_righe_vuote_saltate():
    csv_con_vuota = CSV_ESEMPIO + "IT001E00000001;20260501;004500;2;;;;;;0,0;5,004;;E;\n"
    zip_bytes = _zip_da_csv("C_test.csv", csv_con_vuota)
    risultati = parse_curve_zip(zip_bytes)
    # La riga con ATTIVA_PRELEVATA vuoto va saltata, non deve alzare errori
    # né generare un punto con valore None/vuoto
    assert len(risultati["IT001E00000001"].punti) == 3


def test_parse_curve_zip_timestamp_non_valido_non_blocca_il_resto():
    csv_corrotto = CSV_ESEMPIO + "IT001E00000001;ABCDEFGH;000000;2;0,05;;;;;0,0;5,004;;E;\n"
    zip_bytes = _zip_da_csv("C_test.csv", csv_corrotto)
    risultati = parse_curve_zip(zip_bytes)
    # Le 3 righe valide originali devono comunque essere presenti
    assert len(risultati["IT001E00000001"].punti) == 3


def test_parse_curve_zip_zip_vuoto():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    risultati = parse_curve_zip(buf.getvalue())
    assert risultati == {}


def test_parse_curve_zip_ignora_file_non_csv():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("leggimi.txt.bak", "qualcosa")
        zf.writestr("C_test.csv", CSV_ESEMPIO)
    risultati = parse_curve_zip(buf.getvalue())
    assert "IT001E00000001" in risultati


def test_parse_letture_zip_formato_confermato():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "IT001E00000001"
    ws.append(
        [
            "Data lettura",
            "Tipo lettura",
            "Lettura",
            "Tipologia Misura",
            "Matricola Contatore",
            "Tipo Misuratore",
            "Energia",
            "Fascia",
        ]
    )
    ws.append(["30/06/2026", "SALDO", "3309", "Energia attiva F3", "123456", "EE GIS", "Energia attiva", "F3"])

    xlsx_buf = io.BytesIO()
    wb.save(xlsx_buf)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("L_test_IT001E00000001.xlsx", xlsx_buf.getvalue())

    risultati = parse_letture_zip(buf.getvalue())
    assert "IT001E00000001" in risultati
    riga = risultati["IT001E00000001"][0]
    assert riga.valore == pytest.approx(3309.0)
    assert riga.fascia == "F3"
    assert riga.data_lettura.month == 6
    assert riga.data_lettura.day == 30


def test_pcf_auth_error_e_sottoclasse_di_pcf_api_error():
    assert issubclass(PcfAuthError, PcfApiError)


def test_pcf_api_error_porta_status_e_data():
    err = PcfApiError("boom", http_status=401, data={"message": "boom"})
    assert err.http_status == 401
    assert err.data == {"message": "boom"}


# Estratto reale dell'export che comprende il cambio ora di primavera del
# 29 marzo 2026: il flag passa da 1 (ora solare) a 2 (ora legale) e Duereti
# riempie con zeri l'ora locale 02:00-02:59, che quel giorno non esiste.
CSV_CAMBIO_ORA = (
    "POD;DATA;ORA;FL_ORA_LEGALE;ATTIVA_PRELEVATA;ATTIVA_IMMESSA;"
    "REATTIVA_CAPACITIVA_IMMESSA;REATTIVA_CAPACITIVA_PRELEVATA;"
    "REATTIVA_INDUTTIVA_IMMESSA;REATTIVA_INDUTTIVA_PRELEVATA;"
    "PICCO_PRELEVATA;CONSUMO_PICCO_IMMESSA;TIPO_DATO;\n"
    "IT001E00000001;20260329;010000;1;0,055;;;;;0,0;5,004;;E;\n"
    "IT001E00000001;20260329;011500;1;0,028;;;;;0,0;5,004;;E;\n"
    "IT001E00000001;20260329;013000;1;0,026;;;;;0,0;5,004;;E;\n"
    "IT001E00000001;20260329;014500;1;0,038;;;;;0,0;5,004;;E;\n"
    "IT001E00000001;20260329;020000;2;0,0;;;;;0,0;5,004;;E;\n"
    "IT001E00000001;20260329;021500;2;0,0;;;;;0,0;5,004;;E;\n"
    "IT001E00000001;20260329;023000;2;0,0;;;;;0,0;5,004;;E;\n"
    "IT001E00000001;20260329;024500;2;0,0;;;;;0,0;5,004;;E;\n"
    "IT001E00000001;20260329;030000;2;0,027;;;;;0,0;5,004;;E;\n"
)


class TestFlagOraLegale:
    """La colonna FL_ORA_LEGALE indica l'offset UTC: 1 = ora solare (CET),
    2 = ora legale (CEST). Verificato su un export di 181 giorni comprendente
    il cambio ora di marzo 2026."""

    def test_il_flag_viene_conservato(self):
        risultati = parse_curve_zip(_zip_da_csv("C_t.csv", CSV_CAMBIO_ORA))
        punti = risultati["IT001E00000001"].punti
        assert {p.ora_legale for p in punti} == {"1", "2"}

    def test_il_flag_cambia_alle_due_del_mattino(self):
        """Il 29 marzo alle 02:00 locali scatta l'ora legale."""
        punti = parse_curve_zip(_zip_da_csv("C_t.csv", CSV_CAMBIO_ORA))[
            "IT001E00000001"
        ].punti
        prima = [p for p in punti if p.timestamp.hour < 2]
        dopo = [p for p in punti if p.timestamp.hour >= 2]
        assert all(p.ora_legale == "1" for p in prima)
        assert all(p.ora_legale == "2" for p in dopo)

    def test_l_ora_inesistente_e_a_zero(self):
        """Duereti mantiene 96 intervalli anche nel giorno che ne ha 92,
        riempiendo con zeri l'ora locale che non esiste. Il parser scarta i
        valori vuoti ma non gli zeri espliciti, che restano come punti validi
        a consumo nullo."""
        punti = parse_curve_zip(_zip_da_csv("C_t.csv", CSV_CAMBIO_ORA))[
            "IT001E00000001"
        ].punti
        fantasma = [p for p in punti if p.timestamp.hour == 2]
        assert len(fantasma) == 4
        assert all(p.valore_kwh == 0 for p in fantasma)
