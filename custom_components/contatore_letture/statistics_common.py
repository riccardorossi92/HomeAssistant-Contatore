"""Parte comune dell'import delle curve di consumo come external statistics.

Ogni distributore aggrega i propri campioni (formati e fusi orari diversi)
in bucket orari con una propria `_aggrega_per_ora`, poi delega qui la parte
che è identica per tutti: generare lo statistic_id, rileggere la serie già
presente, fonderla con i dati nuovi, ricalcolare le somme progressive e
scrivere in Home Assistant. Il ricalcolo (invece di accodare soltanto) rende
irrilevante l'ordine di importazione - vedi il docstring di
distributors/pcf_common/statistics.py per i dettagli.

Le somme si ricalcolano sempre come se si partisse da zero, ma si scrivono
solo le ore dalla prima ora nuova in poi: quelle prima non cambiano. Se la
serie letta non e' coerente, o con RISCRITTURA_PARZIALE spento, si riscrive
tutta come nelle versioni precedenti.
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

# Interruttore: False riporta ogni import alla riscrittura dell'intera serie,
# com'era fino alla 0.17. Se la scrittura parziale desse problemi su dati
# reali, si torna indietro cambiando questa riga.
RISCRITTURA_PARZIALE = True

# Scarto ammesso tra la somma progressiva salvata e quella attesa (kWh). Le
# somme scritte da questo modulo coincidono esattamente (stesse addizioni,
# double nel database): la tolleranza copre solo arrotondamenti di chi
# avesse scritto la serie in altro modo.
_TOLLERANZA_SOMMA = 1e-6


def sanitize_statistic_id(pod: str, tipo: str = "energia") -> str:
    """Genera uno statistic_id valido a partire dal codice POD.

    Stesso formato per tutti i distributori: convivono così in modo
    uniforme nella Energy Dashboard, che non distingue quale di loro ha
    popolato lo statistic_id di un dato POD.
    """
    slug = re.sub(r"[^a-z0-9_]", "_", pod.lower())
    return f"{DOMAIN}:{slug}_{tipo}"


async def leggi_serie_esistente(hass: HomeAssistant, statistic_id: str) -> list[dict]:
    """Rilegge tutta la serie oraria già presente per uno statistic_id.

    Restituisce le righe in ordine di tempo come
    {"start": inizio_ora_utc, "state": kwh_dell_ora, "sum": somma_progressiva},
    con None dove il valore manca. Serve per poter reinserire dati storici:
    vedi async_scrivi_serie_oraria.

    Prima di leggere aspetta che il recorder abbia svuotato la coda:
    async_add_external_statistics accoda la scrittura senza attenderla, e
    un recupero storico a blocchi chiama async_scrivi_serie_oraria più volte
    di fila. Senza l'attesa il blocco successivo rilegge una serie parziale
    e ne ricalcola le somme progressive, che ripartono da quasi zero a ogni
    confine di blocco (issue #10).
    """
    recorder = get_instance(hass)
    await recorder.async_block_till_done()
    inizio_epoca = dt_util.utc_from_timestamp(0)
    esistenti = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        inizio_epoca,
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return [
        {
            "start": dt_util.utc_from_timestamp(riga["start"]),
            "state": None if riga.get("state") is None else float(riga["state"]),
            "sum": None if riga.get("sum") is None else float(riga["sum"]),
        }
        for riga in esistenti.get(statistic_id, [])
    ]


def _somme_coerenti(righe: list[dict]) -> bool:
    """True se la serie e' esattamente quella che darebbe il ricalcolo da zero:
    nessun valore mancante, la prima somma uguale al primo state e ogni somma
    uguale alla precedente piu' lo state della sua ora.

    Le serie scritte da questo modulo lo sono sempre. Non lo sono, per
    esempio, quelle rimaste dalle versioni con l'issue #10 (somme che
    ripartivano da zero a ogni blocco del recupero storico) o una somma
    corretta a mano dagli strumenti di Home Assistant.
    """
    precedente = 0.0
    for riga in righe:
        if riga["state"] is None or riga["sum"] is None:
            return False
        attesa = precedente + riga["state"]
        if abs(riga["sum"] - attesa) > _TOLLERANZA_SOMMA + 1e-9 * abs(attesa):
            return False
        precedente = riga["sum"]
    return True


def _con_somme(serie: dict[datetime, float], base: float) -> list[dict]:
    """Righe {start, state, sum} in ordine di tempo, somma progressiva da base."""
    running_sum = base
    stats = []
    for inizio_ora in sorted(serie):
        running_sum += serie[inizio_ora]
        stats.append({"start": inizio_ora, "state": serie[inizio_ora], "sum": running_sum})
    return stats


async def async_scrivi_serie_oraria(
    hass: HomeAssistant,
    pod: str,
    statistic_id: str,
    nome: str,
    nuove_ore: dict[datetime, float],
) -> date | None:
    """Fonde nuove_ore con la serie già presente e scrive le external
    statistics con le somme progressive ricalcolate.

    I dati nuovi hanno la precedenza su quelli già presenti per la stessa
    ora (es. una rettifica del distributore). Restituisce la data locale
    dell'ultimo punto della serie risultante, o None se non c'è nulla.

    Il risultato è sempre quello del ricalcolo da zero di tutta la serie.
    Cambia solo quante righe si riscrivono:
    - se la serie letta e' coerente (vedi _somme_coerenti), le ore prima
      della prima ora nuova non cambiano ne' di state ne' di sum, quindi si
      riparte dalla somma dell'ultima di loro e si riscrive solo da li' in
      poi: l'import giornaliero tocca qualche decina di righe invece di
      tutta la storia del POD;
    - altrimenti, se nuove_ore e' vuoto o con RISCRITTURA_PARZIALE spento, si
      riscrive tutta la serie da zero come nelle versioni precedenti. Cosi'
      una serie danneggiata (issue #10) si ripara al primo import, come prima.
    """
    righe = await leggi_serie_esistente(hass, statistic_id)
    ore_gia_presenti = len(righe)

    if nuove_ore and RISCRITTURA_PARZIALE and _somme_coerenti(righe):
        prima_ora = min(nuove_ore)
        precedenti = [riga for riga in righe if riga["start"] < prima_ora]
        base = precedenti[-1]["sum"] if precedenti else 0.0
        serie = {riga["start"]: riga["state"] for riga in righe if riga["start"] >= prima_ora}
        serie.update(nuove_ore)
        stats = _con_somme(serie, base)
        come = f"riscritte {len(stats)} ore dal {dt_util.as_local(prima_ora).isoformat()}"
    else:
        serie = {riga["start"]: riga["state"] for riga in righe if riga["state"] is not None}
        serie.update(nuove_ore)
        if not serie:
            return None
        stats = _con_somme(serie, 0.0)
        come = f"serie riscritta per intero con {len(stats)} ore"

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
        "POD %s (%s): %d ore nuove/aggiornate, %s (erano %d), ultimo punto %s",
        pod,
        statistic_id,
        len(nuove_ore),
        come,
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
