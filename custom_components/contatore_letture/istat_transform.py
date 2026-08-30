"""Trasformazione pura del CSV ISTAT in albero regione/provincia/comune.

Nessuna dipendenza da Home Assistant o da librerie di rete: usata sia da
istat_comuni.py (fetch live a runtime, dentro l'integrazione) sia da
scripts/update_istat_snapshot.py (rigenerazione dello snapshot bundlato,
nella GitHub Action) - stessa funzione, cosi' le due sorgenti non possono
disallinearsi nel formato prodotto.

Fonte: permalink ufficiale ISTAT "Elenco dei comuni italiani" (CSV),
dichiarato dall'ISTAT stesso immutabile ad ogni aggiornamento del file:
https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv

Prima si usava il mirror JSON https://github.com/matteocontrini/comuni-json,
abbandonato perche' fermo al 01/01/2020 (7.904 comuni contro i 7.894
attuali).

Il CSV ISTAT e' in codifica cp1252 con separatore ';' - la decodifica e'
responsabilita' del chiamante (vedi istat_comuni.py), qui si riceve gia'
testo decodificato.
"""
from __future__ import annotations

import csv
import io

ComuniTree = dict[str, dict[str, dict[str, dict[str, str]]]]

# Le colonne vengono cercate per PREFISSO del nome normalizzato, non per
# posizione: ISTAT ha gia' cambiato la struttura del file in passato
# (giugno 2020, dichiarato sulla loro pagina) e alcune intestazioni
# contengono note a pie' di pagina ("(1)", "(3)"), newline interni e spazi
# finali che e' meglio non replicare letteralmente.
_COLONNE = {
    "codice_regione": "codice regione",
    "nome_regione": "denominazione regione",
    "codice_provincia": "codice provincia",
    "nome_provincia": "denominazione dell'unità territoriale sovracomunale",
    "codice_comune": "codice comune formato alfanumerico",
    "nome_comune": "denominazione in italiano",
}


def _normalizza(intestazione: str) -> str:
    """Minuscolo, senza newline interni e spazi ridondanti: rende il match
    robusto a note a pie' di pagina e spaziature variabili."""
    return " ".join(intestazione.replace("\n", " ").split()).lower()


def _indici_colonne(intestazioni: list[str]) -> dict[str, int]:
    """Mappa nome logico -> indice di colonna, cercando per prefisso.

    Solleva ValueError elencando cosa manca, cosi' se ISTAT cambia di
    nuovo la struttura l'errore dice esattamente quale colonna non e'
    stata trovata invece di fallire con un IndexError oscuro.
    """
    normalizzate = [_normalizza(i) for i in intestazioni]
    indici: dict[str, int] = {}
    for chiave, prefisso in _COLONNE.items():
        for i, intestazione in enumerate(normalizzate):
            if intestazione.startswith(prefisso):
                indici[chiave] = i
                break

    mancanti = set(_COLONNE) - set(indici)
    if mancanti:
        raise ValueError(
            "Colonne ISTAT attese non trovate: "
            + ", ".join(f"{m} (prefisso {_COLONNE[m]!r})" for m in sorted(mancanti))
            + f". Intestazioni presenti nel file: {intestazioni!r}"
        )
    return indici


def comuni_csv_to_tree(testo_csv: str) -> ComuniTree:
    """Converte il CSV ISTAT (gia' decodificato) in
    {regione: {provincia: {comune: codici}}}.

    Righe incomplete o senza codice comune vengono saltate silenziosamente:
    il file ISTAT contiene in fondo alcune righe di note/legenda che non
    sono comuni (7896 righe di dati grezzi contro 7894 comuni reali al
    21/02/2026).

    Solleva ValueError se il file e' vuoto, se mancano colonne attese, o se
    non e' stato estratto nessun comune (segnale che il formato e' cambiato
    in modo incompatibile e va aggiornata questa funzione).
    """
    righe = list(csv.reader(io.StringIO(testo_csv), delimiter=";"))
    if not righe:
        raise ValueError("CSV ISTAT vuoto")

    indici = _indici_colonne(righe[0])
    massimo_indice = max(indici.values())

    tree: ComuniTree = {}
    for riga in righe[1:]:
        if len(riga) <= massimo_indice:
            continue  # riga di nota/legenda in fondo al file, non un comune

        valori = {chiave: riga[i].strip() for chiave, i in indici.items()}
        if not valori["codice_comune"] or not valori["nome_comune"]:
            continue
        if not valori["nome_regione"] or not valori["nome_provincia"]:
            continue

        tree.setdefault(valori["nome_regione"], {}).setdefault(
            valori["nome_provincia"], {}
        )[valori["nome_comune"]] = {
            "codice_regione": valori["codice_regione"],
            "codice_provincia": valori["codice_provincia"],
            "codice_comune": valori["codice_comune"],
        }

    if not tree:
        raise ValueError(
            "Nessun comune estratto dal CSV ISTAT: formato probabilmente cambiato"
        )
    return tree
