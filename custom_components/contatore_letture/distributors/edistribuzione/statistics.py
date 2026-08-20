"""Import delle curve E-Distribuzione come external statistics in Home Assistant.

Schema JSON confermato su una risposta reale di
EdistribuzioneApiClient.async_get_daily_load_profile il 20/08/2026 (POD
IT001E10684497, giorno 2026-08-01):

    [
      {
        "readings": {
          "energyType": "A1",
          "sampleDate": "20260801",
          "sampleValues": [
            {"id": "1", "val": "0.359"},
            ...
            {"id": "96", "val": "0.020"}
          ]
        },
        "sampleFrequency": 15,
        "timeType": "CONS",
        "initialSample": "2026-07-31T22:00:00.000+00:00"
      }
    ]

A differenza del CSV Duereti/Unareti (pcf_common/statistics.py), qui NON
serve interpretare nessun flag per il cambio ora legale: 'initialSample' è
già un timestamp UTC assoluto e completo (con offset esplicito, id=1),
quindi il timestamp di ogni campione si ottiene sommando
(id-1) * sampleFrequency minuti - deterministico, nessuna ambiguità
stagionale. Verificato aritmeticamente sui dati reali: id=1 cade
esattamente a mezzanotte locale del giorno richiesto, id=96 sull'ultimo
quarto d'ora dello stesso giorno locale (23:45-24:00).

ASSUNZIONI NON ANCORA CONFERMATE (nessuna documentazione ufficiale, solo
osservazione su un giorno di un solo POD):

- 'val' è trattato come energia in kWh per intervallo di 15 minuti (non
  potenza media in kW), coerente con l'ordine di grandezza osservato
  (0.02-0.36) per un'utenza domestica e con il nome "curva di carico"
  (load profile), che nel dominio energetico italiano indica tipicamente
  energia per intervallo. Se si rivelasse potenza, i totali giornalieri
  importati sarebbero sbagliati.
- 'timeType' osservato finora: solo "CONS" (consumo). Entrambi i POD di
  test hanno HasPlant=False (nessun impianto di produzione); un POD con
  impianto potrebbe restituire un valore diverso (es. "PROD" per
  l'energia immessa in rete) che questo modulo non distingue ancora -
  oggi somma comunque tutto in un'unica serie di "energia", da rivedere
  se emerge un caso reale con impianto.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta

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
    """Stesso formato di pcf_common.statistics: Duereti/Unareti/
    E-Distribuzione convivono così in modo uniforme nella Energy
    Dashboard, che non distingue quale distributore ha popolato lo
    statistic_id di un dato POD."""
    slug = re.sub(r"[^a-z0-9_]", "_", pod.lower())
    return f"{DOMAIN}:{slug}_energia"


def _aggrega_per_ora(giorni: list[dict]) -> list[tuple[datetime, float]]:
    """Aggrega i campioni a 15 minuti di uno o più giorni in bucket orari.

    Riceve la lista grezza restituita da
    EdistribuzioneApiClient.async_get_daily_load_profile (un dict per
    giorno richiesto). Vedi il docstring del modulo per lo schema atteso
    e come si calcola il timestamp di ogni campione.

    Righe con campi mancanti o non parsabili vengono scartate con un
    warning invece di far fallire l'intero import: un singolo campione
    corrotto non deve perdere il resto della giornata.

    Restituisce una lista di (inizio_ora_utc_aware, kwh_totali) ordinata.
    """
    bucket: dict[datetime, float] = defaultdict(float)

    for giorno in giorni:
        readings = giorno.get("readings", {})
        sample_values = readings.get("sampleValues", [])
        frequenza_minuti = giorno.get("sampleFrequency")
        initial_sample = giorno.get("initialSample")

        if not sample_values or not frequenza_minuti or not initial_sample:
            _LOGGER.debug(
                "Giorno senza dati utilizzabili (sampleValues/sampleFrequency/"
                "initialSample mancanti o vuoti): %r",
                {
                    "sampleValues": bool(sample_values),
                    "sampleFrequency": frequenza_minuti,
                    "initialSample": initial_sample,
                },
            )
            continue

        try:
            inizio_campione_1 = datetime.fromisoformat(initial_sample)
        except ValueError:
            _LOGGER.warning(
                "initialSample non parsabile come data ISO, giorno saltato: %r",
                initial_sample,
            )
            continue

        for campione in sample_values:
            try:
                indice = int(campione["id"])
                valore_kwh = float(campione["val"])
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("Campione con id/val non validi, saltato: %r", campione)
                continue

            ts_utc = dt_util.as_utc(
                inizio_campione_1 + timedelta(minutes=(indice - 1) * frequenza_minuti)
            )
            inizio_ora = ts_utc.replace(minute=0, second=0, microsecond=0)
            bucket[inizio_ora] += valore_kwh

    return sorted(bucket.items())


async def _leggi_serie_esistente(hass: HomeAssistant, statistic_id: str) -> dict:
    """Identica a pcf_common.statistics: rilegge tutta la serie oraria già
    presente per uno statistic_id, {inizio_ora_utc: kwh_dell_ora}."""
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

    serie: dict = {}
    for riga in esistenti.get(statistic_id, []):
        stato = riga.get("state")
        if stato is None:
            continue
        serie[dt_util.utc_from_timestamp(riga["start"])] = float(stato)
    return serie


async def async_import_curva_giornaliera(
    hass: HomeAssistant, pod: str, dati_grezzi: list[dict]
) -> "date | None":  # noqa: F821
    """Importa i campioni a 15 minuti di E-Distribuzione come external
    statistics, aggregandoli in bucket orari.

    Stessa logica di fusione/ricalcolo cumulativo di
    pcf_common.statistics.async_import_curva: rilegge la serie esistente,
    la fonde con i nuovi dati (quelli nuovi hanno la precedenza in caso di
    sovrapposizione) e ricalcola tutte le somme progressive da zero, così
    l'ordine di importazione (storico prima o dopo i dati recenti) non
    influisce sul risultato finale.

    Restituisce la data locale dell'ultimo punto della serie risultante,
    o None se non c'è nulla da importare.
    """
    if not dati_grezzi:
        _LOGGER.debug("Nessun dato curva da importare per POD %s", pod)
        return None

    statistic_id = _sanitize_statistic_id(pod)
    nuove_ore = dict(_aggrega_per_ora(dati_grezzi))

    if not nuove_ore:
        _LOGGER.warning(
            "POD %s: nessun campione valido trovato nella risposta (schema "
            "cambiato rispetto a quello confermato il 20/08/2026?). "
            "Risposta grezza: %r",
            pod,
            dati_grezzi,
        )
        return None

    _LOGGER.debug(
        "POD %s: %d campioni a 15 minuti aggregati in %d ore (%s -> %s)",
        pod,
        sum(len(g.get("readings", {}).get("sampleValues", [])) for g in dati_grezzi),
        len(nuove_ore),
        min(nuove_ore).isoformat(),
        max(nuove_ore).isoformat(),
    )

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
        "name": f"E-Distribuzione {pod}",
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


async def async_get_ultima_data_disponibile(hass: HomeAssistant, pod: str):
    """Identica a pcf_common.statistics: ultima data (locale) effettivamente
    presente nelle external statistics per il POD, o None se non c'è
    ancora nessun dato importato."""
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
