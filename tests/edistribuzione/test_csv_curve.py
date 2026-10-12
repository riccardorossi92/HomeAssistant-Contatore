"""Test per distributors/edistribuzione/csv_curve.py: lettura del CSV della
curva di carico esportato dal portale E-Distribuzione.

csv_curve.py non dipende da Home Assistant: lo si carica direttamente via
importlib, come auth.py in test_auth.py.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_PERCORSO = (
    Path(__file__).parent.parent.parent
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "edistribuzione"
    / "csv_curve.py"
)


def _carica():
    nome = "edistribuzione_csv_curve_test"
    if nome in sys.modules:
        return sys.modules[nome]
    spec = importlib.util.spec_from_file_location(nome, _PERCORSO)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


cc = _carica()
FUSO = ZoneInfo("Europe/Rome")

COLONNE = [
    f"{(i * 15) // 60:02d}:{(i * 15) % 60:02d}-{((i + 1) * 15) // 60 % 24:02d}:{((i + 1) * 15) % 60:02d}"
    for i in range(96)
]
INTESTAZIONE = "Giorno;" + ";".join(COLONNE)


def _riga(giorno: str, valori: list[str | None]) -> str:
    """Una riga come nell'export reale: valori tra virgolette e ';' finale."""
    celle = [f'"{v}"' if v is not None else "" for v in valori]
    return f'"{giorno}";' + ";".join(celle) + ";"


def _csv(*righe: str) -> bytes:
    return ("\n".join([INTESTAZIONE, *righe]) + "\n").encode()


def test_intestazione_come_l_export_reale():
    assert COLONNE[0] == "00:00-00:15"
    assert COLONNE[-1] == "23:45-00:00"


def test_giorno_normale_aggregato_in_ore_utc():
    """Primi quattro quarti d'ora del 01/08/2026 dell'export reale: in ora
    legale la mezzanotte italiana e' alle 22:00 UTC del giorno prima."""
    valori = ["0,359", "0,358", "0,287", "0,349"] + ["0,02"] * 92
    curva = cc.leggi_csv_curva(_csv(_riga("01/08/2026", valori)), FUSO)

    assert curva.giorni == [date(2026, 8, 1)]
    assert len(curva.ore) == 24
    prima_ora = datetime(2026, 7, 31, 22, tzinfo=UTC)
    assert next(iter(curva.ore)) == prima_ora
    assert curva.ore[prima_ora] == pytest.approx(1.353)
    assert curva.kwh_totali == pytest.approx(1.353 + 92 * 0.02)
    assert curva.avvisi == []


def test_celle_vuote_saltate_e_giorno_vuoto_ignorato():
    """Il giorno in corso compare nell'export ma senza valori."""
    curva = cc.leggi_csv_curva(
        _csv(_riga("01/08/2026", ["0,1"] * 48 + [None] * 48), _riga("02/08/2026", [None] * 96)),
        FUSO,
    )
    assert curva.giorni == [date(2026, 8, 1)]
    assert len(curva.ore) == 12


def test_cambio_ora_legale_23_ore():
    """Formato reale del 29/03/2026: una riga, "0" nelle colonne 02:00-03:00
    che quel giorno non esistono."""
    valori = ["0,1"] * 8 + ["0"] * 4 + ["0,1"] * 84
    curva = cc.leggi_csv_curva(_csv(_riga("29/03/2026", valori)), FUSO)

    assert len(curva.ore) == 23
    assert curva.kwh_totali == pytest.approx(9.2)
    assert curva.avvisi == []
    # 00:00 e 01:00 in ora solare (+01), poi direttamente le 03:00 in ora
    # legale (+02): in UTC le ore restano consecutive.
    assert [o.astimezone(FUSO).strftime("%H%z") for o in list(curva.ore)[:3]] == [
        "00+0100",
        "01+0100",
        "03+0200",
    ]


def test_cambio_ora_legale_valori_nelle_colonne_inesistenti_segnalati():
    valori = ["0,1"] * 96
    curva = cc.leggi_csv_curva(_csv(_riga("29/03/2026", valori)), FUSO)
    assert curva.kwh_totali == pytest.approx(9.2)
    assert any("inesistenti" in a for a in curva.avvisi)


def test_ritorno_ora_solare_25_ore_su_due_righe():
    """Formato reale del 26/10/2025: due righe con la stessa data, la prima
    con 00:00-03:00 in ora legale e poi zeri, la seconda con zeri fino alle
    02:00 e poi 02:00-24:00 in ora solare."""
    legale = ["0,1"] * 8 + ["0,2"] * 4 + ["0"] * 84
    solare = ["0"] * 8 + ["0,3"] * 4 + ["0,1"] * 84
    curva = cc.leggi_csv_curva(
        _csv(_riga("26/10/2025", legale), _riga("26/10/2025", solare)), FUSO
    )

    assert curva.giorni == [date(2025, 10, 26)]
    assert len(curva.ore) == 25
    assert curva.avvisi == []
    ore = [(o.astimezone(FUSO).strftime("%H%z"), round(k, 3)) for o, k in curva.ore.items()]
    assert ore[2:4] == [("02+0200", 0.8), ("02+0100", 1.2)]
    assert curva.kwh_totali == pytest.approx(0.8 + 0.8 + 1.2 + 84 * 0.1)


def test_ritorno_ora_solare_su_una_riga_segnalato():
    curva = cc.leggi_csv_curva(_csv(_riga("26/10/2025", ["0,1"] * 96)), FUSO)
    assert len(curva.ore) == 24
    assert any("26/10/2025" in a for a in curva.avvisi)


def test_bom_e_codifica_windows():
    contenuto = "﻿".encode() + _csv(_riga("01/08/2026", ["0,1"] * 96))
    assert cc.leggi_csv_curva(contenuto, FUSO).giorni == [date(2026, 8, 1)]
    assert cc.leggi_csv_curva(_csv(_riga("01/08/2026", ["0,1"] * 96)).replace(
        b"Giorno", "Giorno".encode("cp1252")
    ), FUSO).giorni == [date(2026, 8, 1)]


@pytest.mark.parametrize(
    ("contenuto", "messaggio"),
    [
        (b"", "vuoto"),
        (b"Data;Valore\n01/08/2026;1\n", "intestazione"),
        (_csv(_riga("32/08/2026", ["0,1"] * 96)), "data non valida"),
        (_csv(_riga("2026-08-01", ["0,1"] * 96)), "data non riconosciuta"),
        (_csv(_riga("01/08/2026", [None] * 96)), "nessun valore"),
    ],
)
def test_file_non_valido(contenuto, messaggio):
    with pytest.raises(cc.CsvCurveError, match=messaggio):
        cc.leggi_csv_curva(contenuto, FUSO)
