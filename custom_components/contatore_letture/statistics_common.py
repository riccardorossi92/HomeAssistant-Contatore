"""Parte comune dell'import delle curve di consumo come external statistics.

Ogni distributore aggrega i propri campioni (formati e fusi orari diversi)
in bucket orari con una propria `_aggrega_per_ora`, poi delega qui la parte
che è identica per tutti: generare lo statistic_id, rileggere la serie già
presente, fonderla con i dati nuovi, ricalcolare le somme progressive da
zero e scrivere in Home Assistant. Il ricalcolo totale (invece di accodare
soltanto) rende irrilevante l'ordine di importazione - vedi il docstring di
distributors/pcf_common/statistics.py per i dettagli.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def sanitize_statistic_id(pod: str) -> str:
    """Genera uno statistic_id valido a partire dal codice POD.

    Stesso formato per tutti i distributori: convivono così in modo
    uniforme nella Energy Dashboard, che non distingue quale di loro ha
    popolato lo statistic_id di un dato POD.
    """
    slug = re.sub(r"[^a-z0-9_]", "_", pod.lower())
    return f"{DOMAIN}:{slug}_energia"


async def leggi_serie_esistente(hass: HomeAssistant, statistic_id: str) -> dict[datetime, float]:
    """Rilegge tutta la serie oraria già presente per uno statistic_id.

    Restituisce {inizio_ora_utc: kwh_dell_ora}. Serve per poter reinserire
    dati storici: vedi async_scrivi_serie_oraria.
    """
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


async def async_scrivi_serie_oraria(
    hass: HomeAssistant,
    pod: str,
    statistic_id: str,
    nome: str,
    nuove_ore: dict[datetime, float],
) -> date | None:
    """Fonde nuove_ore con la serie già presente e riscrive le external
    statistics, ricalcolando tutte le somme progressive da zero.

    I dati nuovi hanno la precedenza su quelli già presenti per la stessa
    ora (es. una rettifica del distributore). Restituisce la data locale
    dell'ultimo punto della serie risultante, o None se non c'è nulla.
    """
    serie = await leggi_serie_esistente(hass, statistic_id)
    ore_gia_presenti = len(serie)
    serie.update(nuove_ore)

    if not serie:
        return None

    running_sum = 0.0
    stats = []
    for inizio_ora in sorted(serie):
        running_sum += serie[inizio_ora]
        stats.append(
            {
                "start": inizio_ora,
                "state": serie[inizio_ora],
                "sum": running_sum,
            }
        )

    metadata = {
        "has_mean": False,
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": nome,
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


async def async_ultima_data_disponibile(hass: HomeAssistant, statistic_id: str) -> date | None:
    """Ultima data (locale) effettivamente presente nelle external
    statistics per questo statistic_id, o None se non c'è ancora nessun
    dato importato.

    A differenza del campo 'ultimo_aggiornamento' dei coordinator (che
    riflette solo la fine del range richiesto all'API), questa legge lo
    stato reale delle statistiche salvate - utile perché una richiesta può
    restituire dati parziali o vuoti per gli ultimi giorni del periodo.
    """
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    entry = last_stats.get(statistic_id)
    if not entry:
        return None

    start = entry[0].get("start")
    if start is None:
        return None

    # 'start' è un timestamp UTC: lo convertiamo nel fuso locale prima di
    # ricavarne la data, altrimenti a cavallo della mezzanotte si otterrebbe
    # il giorno sbagliato.
    return dt_util.as_local(dt_util.utc_from_timestamp(start)).date()
