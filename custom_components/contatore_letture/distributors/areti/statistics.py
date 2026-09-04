"""Import della curva di carico Areti come external statistics.

Schema di 'elementiCurve' (dentro la risposta di
AretiApiClient.async_get_misurazioni) confermato su dati reali del
04/09/2026 (agosto 2026, POD di test): lista di
{"Value": "0.034", "Ora": "00:00:00", "Data": "2026-08-01"} a 15 minuti,
96 elementi/giorno, in kWh per intervallo (confermato via
unitOfMeasureMapping: EA/UA -> kWh - vedi documentation/areti-protocol.md).

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
import re
from collections import defaultdict
from datetime import date, datetime, time
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _sanitize_statistic_id(pod: str) -> str:
    """Stesso formato di pcf_common/edistribuzione: tutti i distributori
    convivono così in modo uniforme nella Energy Dashboard, che non
    distingue quale di loro ha popolato lo statistic_id di un dato POD."""
    slug = re.sub(r"[^a-z0-9_]", "_", pod.lower())
    return f"{DOMAIN}:{slug}_energia"


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


async def _leggi_serie_esistente(hass: HomeAssistant, statistic_id: str) -> dict[datetime, float]:
    """Identica a pcf_common/edistribuzione: rilegge tutta la serie oraria
    già presente per uno statistic_id, {inizio_ora_utc: kwh_dell_ora}."""
    inizio_epoca = dt_util.utc_from_timestamp(0)
    esistenti = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        inizio_epoca,
        None,
        {statistic_id},
        "hour",
        None,
        {"state"},
    )

    serie: dict[datetime, float] = {}
    for riga in esistenti.get(statistic_id, []):
        stato = riga.get("state")
        if stato is None:
            continue
        serie[dt_util.utc_from_timestamp(riga["start"])] = float(stato)
    return serie


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

    statistic_id = _sanitize_statistic_id(pod)
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

    serie = await _leggi_serie_esistente(hass, statistic_id)
    ore_gia_presenti = len(serie)
    serie.update(nuove_ore)

    running_sum = 0.0
    stats = []
    for inizio_ora in sorted(serie):
        running_sum += serie[inizio_ora]
        stats.append({"start": inizio_ora, "state": serie[inizio_ora], "sum": running_sum})

    metadata = {
        "has_mean": False,
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": f"Areti {pod}",
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "unit_of_measurement": "kWh",
        "unit_class": "energy",
    }

    async_add_external_statistics(hass, metadata, stats)
    ultima_data = dt_util.as_local(stats[-1]["start"]).date()
    _LOGGER.info(
        "POD %s (%s): %d ore nuove/aggiornate, serie riscritta con %d ore totali "
        "(erano %d), ultimo punto %s",
        pod,
        statistic_id,
        len(nuove_ore),
        len(stats),
        ore_gia_presenti,
        ultima_data.isoformat(),
    )
    return ultima_data


async def async_get_ultima_data_disponibile(hass: HomeAssistant, pod: str) -> date | None:
    """Identica a pcf_common/edistribuzione: ultima data (locale)
    effettivamente presente nelle external statistics per il POD, o None
    se non c'è ancora nessun dato importato."""
    statistic_id = _sanitize_statistic_id(pod)
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    entry = last_stats.get(statistic_id)
    if not entry:
        return None

    start = entry[0].get("start")
    if start is None:
        return None

    return dt_util.as_local(dt_util.utc_from_timestamp(start)).date()
