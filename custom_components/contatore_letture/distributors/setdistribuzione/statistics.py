"""Import della curva oraria SET Distribuzione come external statistics.

Schema di un elemento di 'consumptions' nella risposta del dettaglio di un
giorno (SetDistribuzioneApiClient.async_get_consumi_giorno), confermato
sulla cattura reale del 13/10/2026:

    {
      "drillDownAvailable": true,
      "year": 2026, "month": 10, "day": 12, "hour": 13,
      "quarters": [0.268, 0.457, 0.085, 0.072],
      "total": 0.88199997,
      "average": 0.22049999,
      "estimated": false,
      "meterSerialNumber": "...",
      "kConstant": "1"
    }

Un elemento per ora, 24 in un giorno normale. 'total' è la somma dei
quarters, in kWh: il picco di potenza che il portale riporta per lo stesso
giorno ("1,828 kW") è esattamente il quarto d'ora più alto (0,457 kWh) x 4.
'month' qui è 1-based, a differenza del parametro della richiesta.

Cambio ora: mai osservato (la cattura non contiene un giorno da 23 o 25
ore). Si importa un giorno solo se il numero di ore torna con la durata
reale della giornata locale, ricavata dal database dei fusi di Home
Assistant; con 23 o 25 elementi l'ora i-esima è il tempo trascorso dalla
mezzanotte (come verificato per Ireti), non l'ora sul quadrante. Un giorno
che non torna viene scartato con un avviso invece di importare dati
disallineati, e resta in coda (vedi coordinator.py).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...statistics_common import (
    async_scrivi_serie_oraria,
    async_ultima_data_disponibile,
    sanitize_statistic_id,
)

_LOGGER = logging.getLogger(__name__)

_UN_ORA = timedelta(hours=1)


def _mezzanotte_utc(giorno: date) -> datetime:
    return dt_util.as_utc(
        datetime.combine(giorno, time.min).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    )


def ore_attese(giorno: date) -> int:
    """Ore nella giornata locale: 24, o 23/25 nei giorni di cambio ora."""
    return (_mezzanotte_utc(giorno + timedelta(days=1)) - _mezzanotte_utc(giorno)) // _UN_ORA


def _kwh_ora(elemento: dict[str, Any]) -> float | None:
    """'total' dell'ora, o la somma dei quarters se manca."""
    totale = elemento.get("total")
    if totale is not None:
        try:
            return float(totale)
        except (TypeError, ValueError):
            return None
    quarti = elemento.get("quarters")
    if not isinstance(quarti, list) or not quarti:
        return None
    try:
        return sum(float(q) for q in quarti if q is not None)
    except (TypeError, ValueError):
        return None


def ore_del_giorno(consumi: list[dict[str, Any]], giorno: date) -> dict[datetime, float] | None:
    """{inizio_ora_utc: kWh} per un giorno, o None se la risposta non
    contiene un giorno completo e coerente (vedi docstring del modulo)."""
    elementi = [
        c for c in consumi
        if (c.get("year"), c.get("month"), c.get("day")) == (giorno.year, giorno.month, giorno.day)
    ]
    if not elementi:
        return None

    attese = ore_attese(giorno)
    if len(elementi) != attese:
        _LOGGER.warning(
            "Consumi SET Distribuzione del %s: %d ore ricevute, attese %d - giorno "
            "non importato (incompleto o schema cambiato?)",
            giorno, len(elementi), attese,
        )
        return None

    # sorted è stabile: in un giorno da 25 ore l'ora ripetuta resta
    # nell'ordine in cui arriva.
    ordinati = sorted(elementi, key=lambda c: c.get("hour") if isinstance(c.get("hour"), int) else -1)
    mezzanotte = _mezzanotte_utc(giorno)
    ore: dict[datetime, float] = {}
    for indice, elemento in enumerate(ordinati):
        kwh = _kwh_ora(elemento)
        if kwh is None:
            _LOGGER.warning(
                "Consumi SET Distribuzione del %s: ora %r senza valore leggibile - "
                "giorno non importato",
                giorno, elemento.get("hour"),
            )
            return None
        ore[mezzanotte + indice * _UN_ORA] = kwh
    if any(c.get("estimated") for c in elementi):
        _LOGGER.debug("Consumi SET Distribuzione del %s: contiene valori stimati", giorno)
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
        hass, pod, sanitize_statistic_id(pod), f"SET Distribuzione {pod}", nuove_ore
    )


async def async_get_ultima_data_disponibile(hass: HomeAssistant, pod: str) -> date | None:
    """Ultima data (locale) presente nelle external statistics del POD."""
    return await async_ultima_data_disponibile(hass, sanitize_statistic_id(pod))
