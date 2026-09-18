"""Import delle curve E-Distribuzione come external statistics in Home Assistant.

Schema JSON confermato su una risposta reale di
EdistribuzioneApiClient.async_get_daily_load_profile il 20/08/2026 (un
POD di test, giorno 2026-08-01):

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

CONFERMATO il 21/08/2026: 'val' è energia in kWh per intervallo di 15
minuti, non potenza media in kW. Verificato confrontando il totale della
curva per un mese intero (giugno 2026, stesso POD di test) con il delta
di due letture ufficiali consecutive (1 giugno -> 1 luglio, via
async_get_reading): 134.925 kWh in entrambi i casi, combacianti fino alla
terza cifra decimale. Non una stima approssimativa: un confronto diretto
con dati reali.

ASSUNZIONE ANCORA NON CONFERMATA (nessuna documentazione ufficiale, solo
osservazione su due POD):

- 'timeType' osservato finora: solo "CONS" (consumo). Entrambi i POD di
  test hanno HasPlant=False (nessun impianto di produzione); un POD con
  impianto potrebbe restituire un valore diverso (es. "PROD" per
  l'energia immessa in rete) che questo modulo non distingue ancora -
  oggi somma comunque tutto in un'unica serie di "energia", da rivedere
  se emerge un caso reale con impianto.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...statistics_common import (
    async_scrivi_serie_oraria,
    async_ultima_data_disponibile,
    sanitize_statistic_id,
)

_LOGGER = logging.getLogger(__name__)


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


async def async_import_curva_giornaliera(
    hass: HomeAssistant, pod: str, dati_grezzi: list[dict]
) -> date | None:
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

    statistic_id = sanitize_statistic_id(pod)
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

    return await async_scrivi_serie_oraria(
        hass, pod, statistic_id, f"E-Distribuzione {pod}", nuove_ore
    )


async def async_get_ultima_data_disponibile(hass: HomeAssistant, pod: str) -> date | None:
    """Ultima data (locale) effettivamente presente nelle external
    statistics per il POD, o None se non c'è ancora nessun dato importato."""
    return await async_ultima_data_disponibile(hass, sanitize_statistic_id(pod))
