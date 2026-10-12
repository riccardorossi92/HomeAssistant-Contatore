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
saltate. I giorni del cambio ora NON sono ancora verificati su un export
reale: con 96 colonne fisse, il giorno da 23 ore ha 4 quarti d'ora che non
esistono (02:00-03:00) e quello da 25 ore uno in piu' che non ha colonna.
Vedi _slot_del_giorno per come vengono trattati.
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


def _slot_del_giorno(
    giorno: date, valori: list[float | None], fuso: ZoneInfo, avvisi: list[str]
) -> list[tuple[datetime, float]]:
    """(inizio quarto d'ora in UTC, kWh) per i valori di un giorno.

    In un giorno normale la colonna i inizia a mezzanotte locale + i*15
    minuti. Nei giorni del cambio ora le colonne restano 96 e i quarti
    d'ora reali sono 92 o 100:
    - 23 ore (marzo): se le 4 colonne 02:00-03:00 sono vuote si saltano; se
      invece sono vuote le ultime 4, i 92 valori sono in sequenza dalla
      mezzanotte. In entrambi i casi il tempo e' quello trascorso dalla
      mezzanotte, come nella risposta API.
    - 25 ore (ottobre): una colonna per ora non basta; si usa l'ora indicata
      dalla colonna (02:00-03:00 riferita alla prima occorrenza), e il
      giorno viene segnalato perche' un'ora potrebbe mancare.
    """
    mezzanotte = datetime(giorno.year, giorno.month, giorno.day, tzinfo=fuso)
    domani = mezzanotte + timedelta(days=1)
    domani = datetime(domani.year, domani.month, domani.day, tzinfo=fuso)
    quarti_reali = int((domani.astimezone(UTC) - mezzanotte.astimezone(UTC))
                       / timedelta(minutes=15))
    inizio_utc = mezzanotte.astimezone(UTC)

    def in_sequenza(sequenza: list[float | None]) -> list[tuple[datetime, float]]:
        return [
            (inizio_utc + timedelta(minutes=15 * i), v)
            for i, v in enumerate(sequenza)
            if v is not None
        ]

    if quarti_reali == QUARTI_D_ORA:
        return in_sequenza(valori)

    if quarti_reali == QUARTI_D_ORA - 4:
        if all(v is None for v in valori[8:12]):
            return in_sequenza(valori[:8] + valori[12:])
        if all(v is None for v in valori[-4:]):
            return in_sequenza(valori[:-4])
        avvisi.append(
            f"{giorno:%d/%m/%Y} (cambio all'ora legale): valori presenti in tutte le "
            "colonne, ultimo quarto d'ora ignorato. Formato non ancora verificato."
        )
        return in_sequenza(valori[:-4])

    avvisi.append(
        f"{giorno:%d/%m/%Y} (ritorno all'ora solare): il file ha 96 colonne per 100 "
        "quarti d'ora, un'ora potrebbe mancare. Formato non ancora verificato."
    )
    slot = []
    for i, v in enumerate(valori):
        if v is None:
            continue
        ora_locale = (datetime.min + timedelta(minutes=15 * i)).time()
        locale = datetime.combine(giorno, ora_locale, tzinfo=fuso)
        slot.append((locale.astimezone(UTC), v))
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
    ore: dict[datetime, float] = defaultdict(float)
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

        slot = _slot_del_giorno(giorno, valori, fuso, risultato.avvisi)
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
