"""Import della curva di carico Areti come external statistics.

Schema di 'elementiCurve' (dentro la risposta di
AretiApiClient.async_get_misurazioni) confermato su dati reali del
04/09/2026 (agosto 2026, POD di test): lista di
{"Value": "0.034", "Ora": "00:00:00", "Data": "2026-08-01"} a 15 minuti,
96 elementi/giorno, in kWh per intervallo (confermato via
unitOfMeasureMapping: EA/UA -> kWh - vedi documentation/protocols/areti-protocol.md).

A differenza di E-Distribuzione (initialSample = timestamp UTC assoluto,
nessuna ambiguità), qui 'Data'+'Ora' sono ORA LOCALE senza indicazione
esplicita del fuso o di un flag DST: costruiamo un datetime locale
(fuso di Home Assistant) e lo convertiamo in UTC con dt_util.as_utc.

NON VERIFICATO: il comportamento nei due giorni di cambio ora legale
(marzo/ottobre) - la cattura disponibile copre solo agosto 2026, un mese
senza cambio ora. pcf_common/statistics.py gestisce lo stesso problema
per Duereti/Unareti leggendo un flag esplicito nel CSV, che qui non
esiste: se emergono anomalie nei giorni di cambio ora (23 o 25 punti
invece di 24 ore, o orari duplicati/mancanti), è il primo posto da
controllare.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...statistics_common import (
    async_scrivi_serie_oraria,
    async_ultima_data_disponibile,
    sanitize_statistic_id,
)

_LOGGER = logging.getLogger(__name__)


def _aggrega_per_ora(elementi_curve: list[dict[str, Any]]) -> list[tuple[datetime, float]]:
    """Aggrega i campioni a 15 minuti (locali) in bucket orari (UTC).

    Righe con campi mancanti o non parsabili vengono scartate con un
    warning invece di far fallire l'intero import: un singolo campione
    corrotto non deve perdere il resto del mese.
    """
    bucket: dict[datetime, float] = defaultdict(float)

    for elemento in elementi_curve:
        try:
            giorno = date.fromisoformat(elemento["Data"])
            ora_locale = time.fromisoformat(elemento["Ora"])
            valore_kwh = float(elemento["Value"])
        except (KeyError, TypeError, ValueError) as exc:
            _LOGGER.warning("Elemento di curva non valido, scartato: %r (%s)", elemento, exc)
            continue

        locale = datetime.combine(giorno, ora_locale).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        ts_utc = dt_util.as_utc(locale)
        inizio_ora = ts_utc.replace(minute=0, second=0, microsecond=0)
        bucket[inizio_ora] += valore_kwh

    return sorted(bucket.items())


async def async_import_curva_mensile(
    hass: HomeAssistant, pod: str, elementi_curve: list[dict[str, Any]]
) -> date | None:
    """Importa i campioni a 15 minuti di un mese come external statistics,
    aggregati in bucket orari.

    Stessa logica di fusione/ricalcolo cumulativo di pcf_common/
    edistribuzione: rilegge la serie esistente, la fonde con i nuovi dati
    (quelli nuovi hanno la precedenza in caso di sovrapposizione) e
    ricalcola tutte le somme progressive da zero, così l'ordine di
    importazione (mesi storici prima o dopo quelli recenti, es. via
    recupera_storico) non influisce sul risultato finale.

    Restituisce la data locale dell'ultimo punto della serie risultante,
    o None se non c'è nulla da importare.
    """
    if not elementi_curve:
        _LOGGER.debug("Nessun dato curva da importare per POD %s", pod)
        return None

    statistic_id = sanitize_statistic_id(pod)
    nuove_ore = dict(_aggrega_per_ora(elementi_curve))

    if not nuove_ore:
        _LOGGER.warning(
            "POD %s: nessun campione valido trovato in elementiCurve (schema "
            "cambiato rispetto a quello confermato il 04/09/2026?). "
            "Risposta grezza: %r",
            pod,
            elementi_curve,
        )
        return None

    return await async_scrivi_serie_oraria(hass, pod, statistic_id, f"Areti {pod}", nuove_ore)


async def async_get_ultima_data_disponibile(hass: HomeAssistant, pod: str) -> date | None:
    """Ultima data (locale) effettivamente presente nelle external
    statistics per il POD, o None se non c'è ancora nessun dato importato."""
    return await async_ultima_data_disponibile(hass, sanitize_statistic_id(pod))
