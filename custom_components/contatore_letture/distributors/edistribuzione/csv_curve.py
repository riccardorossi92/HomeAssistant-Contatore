"""Lettura del CSV della curva di carico scaricato dal portale E-Distribuzione.

E' l'alternativa al download automatico per la modalita' "caricamento
manuale": l'utente esporta la curva dal sito (dove il browser passa la
protezione antibot) e la carica dalle opzioni dell'integrazione.

Formato, da un export reale di agosto 2026 (ExportData_agosto.csv):

    Giorno;00:00-00:15;00:15-00:30;...;23:45-00:00
    "01/08/2026";"0,359";"0,358";...;"0,02";

- separatore ';', valori tra virgolette con la virgola decimale, una riga
  per giorno in formato GG/MM/AAAA e un ';' finale;
- 96 colonne di quarti d'ora con l'ora locale (italiana) di inizio e fine;
- i valori sono kWh per quarto d'ora, gli stessi della risposta API
  querydailyloadprofile: il primo e l'ultimo valore del 01/08/2026 (0,359 e
  0,020) coincidono con quelli dell'esempio API documentato in statistics.py,
  verificato a sua volta contro le letture ufficiali;
- il file non contiene il POD: e' quello della configurazione su cui lo si
  carica.

Le celle vuote (es. il giorno in corso, non ancora pubblicato) vengono
saltate. I giorni del cambio ora, da export reali di marzo 2026 e ottobre
2025, usano l'ora dell'orologio indicata dalle colonne:
- giorno da 23 ore (29/03/2026): una riga, con "0" nelle colonne
  02:00-03:00 che quel giorno non esistono;
- giorno da 25 ore (26/10/2025): DUE righe con la stessa data. La prima ha
  i valori 00:00-03:00 in ora legale e poi zeri, la seconda zeri fino alle
  02:00 e poi i valori 02:00-24:00 in ora solare: 12 + 88 = 100 quarti
  d'ora.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

QUARTI_D_ORA = 96
_RE_COLONNA = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
_RE_GIORNO = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


class CsvCurveError(Exception):
    """Il file non ha il formato dell'export E-Distribuzione."""


@dataclass
class CurvaDaCsv:
    """Contenuto del CSV, pronto per l'import."""

    # kWh per ora, indicizzati sull'inizio dell'ora in UTC.
    ore: dict[datetime, float] = field(default_factory=dict)
    # Giorni con almeno un valore.
    giorni: list[date] = field(default_factory=list)
    kwh_totali: float = 0.0
    # Messaggi da riportare nei log (giorni del cambio ora, celle illeggibili).
    avvisi: list[str] = field(default_factory=list)


def _decodifica(contenuto: bytes) -> str:
    try:
        return contenuto.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Export aperti e risalvati con Excel su Windows.
        return contenuto.decode("cp1252")


def _numero(cella: str) -> float:
    return float(cella.strip().replace(".", "").replace(",", ".")) if "," in cella else float(cella)


def _istante_utc(giorno: date, colonna: int, fuso: ZoneInfo, fold: int = 0) -> datetime:
    """Inizio in UTC del quarto d'ora della colonna (0 = 00:00-00:15),
    letto come ora dell'orologio italiana. fold=1 sceglie la seconda
    occorrenza delle 02:00-03:00 nel giorno da 25 ore."""
    ora, minuti = divmod(colonna * 15, 60)
    locale = datetime(giorno.year, giorno.month, giorno.day, ora, minuti, tzinfo=fuso, fold=fold)
    return locale.astimezone(UTC)


def _quarti_reali(giorno: date, fuso: ZoneInfo) -> int:
    inizio = datetime(giorno.year, giorno.month, giorno.day, tzinfo=fuso)
    domani = giorno + timedelta(days=1)
    fine = datetime(domani.year, domani.month, domani.day, tzinfo=fuso)
    return int((fine.astimezone(UTC) - inizio.astimezone(UTC)) / timedelta(minutes=15))


# Colonne 02:00-02:45: inesistenti a marzo, doppie a ottobre.
_COLONNE_CAMBIO_ORA = range(8, 12)


def _slot_del_giorno(
    giorno: date, righe: list[list[float | None]], fuso: ZoneInfo, avvisi: list[str]
) -> list[tuple[datetime, float]]:
    """(inizio quarto d'ora in UTC, kWh) per le righe di un giorno.

    Nei giorni normali c'e' una riga e ogni colonna e' l'ora dell'orologio.
    Nei giorni del cambio ora vale il formato descritto nel docstring del
    modulo; le celle che quel formato riempie con "0" segnaposto vengono
    ignorate, con un avviso se contengono altro.
    """
    quarti = _quarti_reali(giorno, fuso)
    slot: list[tuple[datetime, float]] = []

    def aggiungi(valori: list[float | None], colonne, fold: int = 0) -> None:
        for colonna in colonne:
            if valori[colonna] is not None:
                slot.append((_istante_utc(giorno, colonna, fuso, fold), valori[colonna]))

    def segnala_ignorati(valori: list[float | None], colonne, cosa: str) -> None:
        if any(valori[c] for c in colonne):
            avvisi.append(f"{giorno:%d/%m/%Y}: valori non nulli ignorati ({cosa})")

    if quarti == QUARTI_D_ORA or len(righe) > 2:
        if len(righe) > 1:
            avvisi.append(f"{giorno:%d/%m/%Y}: {len(righe)} righe, usata l'ultima")
        aggiungi(righe[-1], range(QUARTI_D_ORA))
        return slot

    valori = righe[0]
    if quarti < QUARTI_D_ORA:
        # Cambio all'ora legale: le 02:00-03:00 non esistono.
        segnala_ignorati(valori, _COLONNE_CAMBIO_ORA, "02:00-03:00 inesistenti")
        aggiungi(valori, [c for c in range(QUARTI_D_ORA) if c not in _COLONNE_CAMBIO_ORA])
        return slot

    # Ritorno all'ora solare.
    if len(righe) == 1:
        avvisi.append(
            f"{giorno:%d/%m/%Y} (ritorno all'ora solare): attese due righe, trovata una; "
            "le 02:00-03:00 sono state lette una sola volta"
        )
        aggiungi(valori, range(QUARTI_D_ORA))
        return slot

    legale, solare = righe
    aggiungi(legale, range(12))
    segnala_ignorati(legale, range(12, QUARTI_D_ORA), "prima riga dopo le 03:00")
    aggiungi(solare, _COLONNE_CAMBIO_ORA, fold=1)
    aggiungi(solare, range(12, QUARTI_D_ORA))
    segnala_ignorati(solare, range(8), "seconda riga prima delle 02:00")
    return slot


def leggi_csv_curva(contenuto: bytes, fuso: ZoneInfo) -> CurvaDaCsv:
    """Legge l'export e aggrega i quarti d'ora in ore UTC.

    Solleva CsvCurveError se l'intestazione non e' quella attesa o se non
    c'e' nessun valore da importare.
    """
    righe = list(csv.reader(io.StringIO(_decodifica(contenuto)), delimiter=";"))
    righe = [r for r in righe if any(c.strip() for c in r)]
    if not righe:
        raise CsvCurveError("il file è vuoto")

    intestazione = [c.strip() for c in righe[0]]
    colonne = intestazione[1 : 1 + QUARTI_D_ORA]
    if (
        not intestazione
        or intestazione[0].lower() != "giorno"
        or len(colonne) != QUARTI_D_ORA
        or not all(_RE_COLONNA.match(c) for c in colonne)
        or colonne[0] != "00:00-00:15"
    ):
        raise CsvCurveError(
            "intestazione non riconosciuta: serve l'export della curva di carico "
            "a quarti d'ora (colonne Giorno;00:00-00:15;...;23:45-00:00)"
        )

    risultato = CurvaDaCsv()
    per_giorno: dict[date, list[list[float | None]]] = {}
    for numero_riga, riga in enumerate(righe[1:], start=2):
        corrispondenza = _RE_GIORNO.match(riga[0].strip())
        if not corrispondenza:
            raise CsvCurveError(f"riga {numero_riga}: data non riconosciuta ({riga[0]!r})")
        gg, mm, aaaa = (int(x) for x in corrispondenza.groups())
        try:
            giorno = date(aaaa, mm, gg)
        except ValueError as err:
            raise CsvCurveError(f"riga {numero_riga}: data non valida ({riga[0]!r})") from err

        valori: list[float | None] = []
        for cella in (riga[1 : 1 + QUARTI_D_ORA] + [""] * QUARTI_D_ORA)[:QUARTI_D_ORA]:
            if not cella.strip():
                valori.append(None)
                continue
            try:
                valori.append(_numero(cella))
            except ValueError:
                risultato.avvisi.append(
                    f"{giorno:%d/%m/%Y}: valore non numerico ignorato ({cella!r})"
                )
                valori.append(None)
        # Il giorno da 25 ore occupa due righe consecutive con la stessa data.
        per_giorno.setdefault(giorno, []).append(valori)

    ore: dict[datetime, float] = defaultdict(float)
    for giorno, righe_giorno in per_giorno.items():
        slot = _slot_del_giorno(giorno, righe_giorno, fuso, risultato.avvisi)
        if not slot:
            continue
        risultato.giorni.append(giorno)
        for istante, kwh in slot:
            ore[istante.replace(minute=0)] += kwh
            risultato.kwh_totali += kwh

    if not ore:
        raise CsvCurveError("nessun valore da importare nel file")

    risultato.ore = dict(sorted(ore.items()))
    risultato.giorni.sort()
    return risultato
