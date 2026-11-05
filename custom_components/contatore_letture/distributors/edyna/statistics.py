"""Import della curva oraria Edyna come external statistics.

Il portale mostra, per mese, una riga per giorno con 25 colonne "Ora 1 ...
Ora 25" in kWh (3 decimali, virgola). Le etichette sono fisse (Ora 1 = 00-01
... Ora 24 = 23-24, Ora 25 = 02-03). Giorno normale: Ora 25 vale 0,000.

Cambio ora - VERIFICATO SOLO IN PARTE. Il 25/10/2026 (fine ora legale, 25
ore) la cattura reale riempie anche l'Ora 25, che vale quanto le altre ore
notturne (0,26 kWh contro 0,25-0,38) e non le ore serali: è la seconda
02-03, messa in coda. Se ne ricava il mapping per il giorno da 25 ore:

    colonne 1-3  -> ore trascorse dalla mezzanotte 0-2
    colonna 25   -> ora trascorsa 3 (la seconda 02-03)
    colonne 4-24 -> ore trascorse 4-24

Il giorno da 23 ore (fine marzo) non è mai stato osservato: si assume che
l'Ora 3 (02-03, inesistente) sia a zero o vuota e che le altre colonne
scorrano di una; se non lo è il giorno non si importa. Un giorno che non
torna con la durata reale della giornata locale (ricavata dal database dei
fusi di Home Assistant) viene scartato con un avviso invece di importare dati
disallineati.

Un giorno a zero in tutte le ore non è un consumo: è un giorno non ancora
pubblicato, e non si importa.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...statistics_common import (
    async_scrivi_serie_oraria,
    async_ultima_data_disponibile,
    sanitize_statistic_id,
)
from .const import COLONNE_ORARIE

_LOGGER = logging.getLogger(__name__)

_UN_ORA = timedelta(hours=1)
# Indici (0-based) delle colonne speciali.
_IDX_ORA_3 = 2
_IDX_ORA_25 = 24


def _mezzanotte_utc(giorno: date) -> datetime:
    return dt_util.as_utc(
        datetime.combine(giorno, time.min).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    )


def ore_attese(giorno: date) -> int:
    """Ore nella giornata locale: 24, o 23/25 nei giorni di cambio ora."""
    return (_mezzanotte_utc(giorno + timedelta(days=1)) - _mezzanotte_utc(giorno)) // _UN_ORA


def _ore_trascorse(attese: int) -> list[int | None]:
    """Per ognuna delle 25 colonne, l'ora trascorsa dalla mezzanotte, o None
    se la colonna non esiste in quel tipo di giornata."""
    if attese == 25:
        return [0, 1, 2, *range(4, 25), 3]
    if attese == 23:
        return [0, 1, None, *range(2, 23), None]
    return [*range(24), None]


def ore_del_giorno(valori: list[float | None], giorno: date) -> dict[datetime, float] | None:
    """{inizio_ora_utc: kWh} per un giorno, o None se la riga non è un giorno
    completo e coerente (vedi docstring del modulo)."""
    if len(valori) != COLONNE_ORARIE:
        _LOGGER.warning(
            "Consumi Edyna del %s: %d colonne ricevute, attese %d - giorno non importato "
            "(schema cambiato?)",
            giorno, len(valori), COLONNE_ORARIE,
        )
        return None

    attese = ore_attese(giorno)
    mappa = _ore_trascorse(attese)
    ore: dict[datetime, float] = {}
    mezzanotte = _mezzanotte_utc(giorno)
    for indice, (valore, trascorse) in enumerate(zip(valori, mappa, strict=True)):
        if trascorse is None:
            # Colonna che quel giorno non dovrebbe contenere nulla.
            if valore not in (None, 0.0):
                _LOGGER.warning(
                    "Consumi Edyna del %s: la colonna %d vale %s ma in un giorno da %d ore "
                    "dovrebbe essere vuota - giorno non importato",
                    giorno, indice + 1, valore, attese,
                )
                return None
            continue
        if valore is None or valore < 0:
            _LOGGER.debug("Consumi Edyna del %s: colonna %d senza valore valido", giorno, indice + 1)
            return None
        ore[mezzanotte + trascorse * _UN_ORA] = valore

    if len(ore) != attese:
        return None
    if not any(ore.values()):
        return None
    return ore


async def async_import_ore(
    hass: HomeAssistant, pod: str, nuove_ore: dict[datetime, float]
) -> date | None:
    """Scrive le ore nelle external statistics del POD, con la stessa
    fusione/ricalcolo delle somme degli altri distributori (vedi
    statistics_common). Ritorna la data locale dell'ultimo punto della
    serie, o None se non c'è nulla da importare."""
    if not nuove_ore:
        return None
    return await async_scrivi_serie_oraria(
        hass, pod, sanitize_statistic_id(pod), f"Edyna {pod}", nuove_ore
    )


async def async_get_ultima_data_disponibile(hass: HomeAssistant, pod: str) -> date | None:
    """Ultima data (locale) presente nelle external statistics del POD."""
    return await async_ultima_data_disponibile(hass, sanitize_statistic_id(pod))
