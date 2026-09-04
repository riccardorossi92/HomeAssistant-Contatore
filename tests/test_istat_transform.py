"""Test della trasformazione CSV ISTAT -> albero regione/provincia/comune.

Funzione pura (nessuna dipendenza da HA): si rompe se ISTAT cambia la
struttura del file, ed e' condivisa tra il fetch a runtime e la GitHub
Action che rigenera lo snapshot, quindi va tenuta sotto controllo.
"""
from __future__ import annotations

import csv
import io

import pytest

from custom_components.contatore_letture.istat_transform import (
    _indici_colonne,
    _normalizza,
    comuni_csv_to_tree,
)

# Intestazioni realistiche: nota a pie' di pagina "(1)", e - come nel file
# vero - un a-capo DENTRO un campo, che quindi e' quotato (altrimenti
# csv.reader lo interpreterebbe come fine riga).
HEADER = (
    "Codice Regione;Denominazione Regione;Codice Provincia (1);"
    '"Denominazione dell\'Unità territoriale sovracomunale \n'
    '(valida a fini statistici)";'
    "Codice Comune formato alfanumerico;Denominazione in italiano"
)


def _csv(*righe: str) -> str:
    return "\n".join([HEADER, *righe])


def test_normalizza_rimuove_acapo_note_e_spazi():
    assert _normalizza("Codice  Regione (1)\n ") == "codice regione (1)"


def test_indici_colonne_trova_per_prefisso():
    # come nel codice reale: le intestazioni passano prima da csv.reader
    # (che toglie le virgolette del campo multilinea).
    intestazioni = next(csv.reader(io.StringIO(HEADER), delimiter=";"))
    idx = _indici_colonne(intestazioni)
    assert idx["codice_regione"] == 0
    assert idx["nome_provincia"] == 3
    assert idx["nome_comune"] == 5


def test_indici_colonne_segnala_le_colonne_mancanti():
    intestazioni = ["Codice Regione", "Denominazione Regione"]
    with pytest.raises(ValueError, match="codice comune"):
        _indici_colonne(intestazioni)


def test_albero_costruito_correttamente():
    tree = comuni_csv_to_tree(
        _csv(
            "03;Lombardia;015;Milano;015242;Vimodrone",
            "03;Lombardia;015;Milano;015146;Milano",
            "07;Liguria;010;Genova;010039;Moneglia",
        )
    )
    assert tree["Lombardia"]["Milano"]["Vimodrone"] == {
        "codice_regione": "03",
        "codice_provincia": "015",
        "codice_comune": "015242",
    }
    assert set(tree["Lombardia"]["Milano"]) == {"Vimodrone", "Milano"}
    assert tree["Liguria"]["Genova"]["Moneglia"]["codice_comune"] == "010039"


def test_ordine_colonne_indifferente():
    header = ";".join(
        [
            "Denominazione in italiano",
            "Codice Comune formato alfanumerico",
            "Denominazione Regione",
            "Codice Regione",
            "Denominazione dell'Unità territoriale sovracomunale",
            "Codice Provincia",
        ]
    )
    tree = comuni_csv_to_tree(f"{header}\nVimodrone;015242;Lombardia;03;Milano;015")
    assert tree["Lombardia"]["Milano"]["Vimodrone"]["codice_comune"] == "015242"


def test_righe_incomplete_o_di_legenda_saltate():
    tree = comuni_csv_to_tree(
        _csv(
            "03;Lombardia;015;Milano;015242;Vimodrone",
            "03;Lombardia;015;Milano;;",  # senza codice/nome comune
            "nota: le denominazioni sono...",  # riga di legenda, troppo corta
        )
    )
    assert tree == {
        "Lombardia": {"Milano": {"Vimodrone": {
            "codice_regione": "03",
            "codice_provincia": "015",
            "codice_comune": "015242",
        }}}
    }


def test_csv_vuoto_solleva_errore():
    with pytest.raises(ValueError, match="vuoto"):
        comuni_csv_to_tree("")


def test_nessun_comune_estratto_solleva_errore():
    with pytest.raises(ValueError, match="Nessun comune"):
        comuni_csv_to_tree(_csv("03;Lombardia;015;Milano;;"))
