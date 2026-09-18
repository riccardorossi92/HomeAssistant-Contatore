"""Import delle curve PCF (Duereti/Unareti) come external statistics in Home Assistant.

Punti chiave dell'implementazione:

- i punti a 15 minuti del CSV vengono aggregati in bucket ORARI, perché le
  external statistics di Home Assistant sono orarie;
- l'aggregazione avviene sull'istante assoluto in UTC, così nell'ora ripetuta
  del cambio ora i due intervalli con lo stesso orario locale restano distinti;
- ad ogni import la serie esistente viene riletta, fusa con i nuovi dati e le
  somme progressive ricalcolate da zero. Costa qualche migliaio di righe lette
  per volta, ma rende irrilevante l'ordine di inserimento: senza, un recupero
  storico di periodi anteriori a quelli già presenti verrebbe scartato o
  produrrebbe salti artificiali nel grafico, dato che la somma è cumulativa.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...statistics_common import (
    async_scrivi_serie_oraria,
    async_ultima_data_disponibile,
    sanitize_statistic_id,
)
from .api import RisultatoLetture

_LOGGER = logging.getLogger(__name__)


# Interpretazione della colonna FL_ORA_LEGALE del CSV: il flag indica
# l'offset UTC in ore, 1 = ora solare (CET), 2 = ora legale (CEST).
#
# Confermata su un export Duereti reale di 181 giorni (1 febbraio - 31 luglio
# 2026, 17376 intervalli a 15 minuti) che comprende il cambio ora del 29
# marzo: il flag passa da "1" a "2" esattamente alle 02:00 locali, e
# convertendo tutti gli intervalli in UTC si ottiene una sequenza oraria
# continua, senza buchi né sfasamenti. Sia il controllo sull'intera sequenza
# di 181 giorni sia quello puntuale sul singolo giorno del cambio ora sono
# stati fatti sul lato Duereti (stessa infrastruttura, stesso formato
# dichiarato), non ancora riverificati su un export reale Unareti: le 4 righe
# segnaposto che l'esportazione inserisce per l'ora inesistente (02:00-02:45,
# flag "2") risultavano sempre a consumo zero, cadono nello stesso bucket UTC
# delle 4 righe reali delle 01:00-01:45 (flag "1"), e il totale giornaliero
# ricostruito coincideva esattamente con la somma diretta della colonna
# ATTIVA_PRELEVATA: 20,278 kWh in entrambi i casi, 23 bucket orari.
#
# _aggrega_per_ora sotto verifica che questa assunzione (segnaposto sempre a
# zero) continui a valere ad ogni import, segnalando con un warning se un
# bucket orario dovesse ricevere più di 4 intervalli con più di 4 non nulli:
# vorrebbe dire che il cambio ora ha prodotto energia reale in un intervallo
# che finora era sempre stato un segnaposto, e il totale potrebbe essere
# sovrastimato.
#
# Non ancora verificato sui dati reali: il cambio di fine ottobre (ora
# ripetuta, fuso che passa da CEST a CET). La logica dovrebbe reggere allo
# stesso modo - lì il flag serve proprio a distinguere le due ore uguali
# invece di farle collassare - ma non c'è stata occasione di controllarlo su
# un export che copra quella data.
#
# Se il flag ha un valore diverso da quelli previsti si ricade sul fuso
# locale di Home Assistant (comportamento precedente), loggando un warning
# una sola volta: così un valore imprevisto degrada in modo prevedibile
# invece di produrre silenziosamente timestamp errati.
_OFFSET_DA_FLAG = {
    "1": 1,  # ora solare (CET)
    "2": 2,  # ora legale (CEST)
}

_flag_sconosciuti_segnalati: set[str] = set()


def _timestamp_aware(punto) -> datetime:
    """Rende timezone-aware il timestamp naive di un punto curva.

    Usa FL_ORA_LEGALE quando è un valore noto, perché è l'unico modo di
    distinguere le due ore identiche del cambio ora d'autunno. Altrimenti
    ricade sul fuso locale, che in quell'unica ora all'anno è ambiguo.
    """
    from datetime import timedelta, timezone

    ts = punto.timestamp
    if ts.tzinfo is not None:
        return ts

    flag = getattr(punto, "ora_legale", None)
    offset = _OFFSET_DA_FLAG.get(flag) if flag else None

    if offset is not None:
        return ts.replace(tzinfo=timezone(timedelta(hours=offset)))

    if flag and flag not in _flag_sconosciuti_segnalati:
        _flag_sconosciuti_segnalati.add(flag)
        _LOGGER.warning(
            "Valore FL_ORA_LEGALE non riconosciuto (%r): uso il fuso locale come ripiego. "
            "Segnalalo come issue: serve a gestire correttamente il cambio ora.",
            flag,
        )
    return ts.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


def _aggrega_per_ora(punti: list) -> list[tuple]:
    """Aggrega i punti curva (intervalli di 15 minuti) in bucket orari.

    Due motivi:

    1. Le external statistics di Home Assistant sono ORARIE: 'start' deve
       cadere sull'inizio dell'ora. Passare timestamp a :15/:30/:45 farebbe
       finire più punti nella stessa ora, sovrascrivendosi a vicenda.
    2. I timestamp del CSV Duereti sono NAIVE (nessun fuso orario) e riferiti
       all'ora locale italiana. HA li rifiuta con "Naive timestamp: no or
       invalid timezone info provided", quindi vanno resi timezone-aware.

    L'aggregazione avviene sull'istante assoluto (UTC), non sull'ora locale:
    nell'ora ripetuta del cambio ora due istanti diversi hanno lo stesso
    orario locale, e raggrupparli per orario locale li fonderebbe. In UTC
    restano distinti, quindi diventano due ore separate come dev'essere.

    Nel giorno del cambio ora di primavera Duereti mantiene i 96 intervalli
    fissi anche se il giorno ne ha 92, riempiendo con zeri l'ora locale che
    non esiste (le 02:00-02:59). Convertiti in UTC quegli intervalli cadono
    sulla stessa ora dei precedenti: sommarli è corretto finché valgono zero,
    quindi lo facciamo, ma se contenessero energia lo segnaliamo perché
    significherebbe che l'assunzione non vale più.

    Restituisce una lista di (inizio_ora_utc_aware, kwh_totali) ordinata.
    """
    bucket: dict = defaultdict(float)
    conteggio: dict = defaultdict(int)
    non_nulli: dict = defaultdict(int)

    for punto in punti:
        ts_utc = dt_util.as_utc(_timestamp_aware(punto))
        inizio_ora = ts_utc.replace(minute=0, second=0, microsecond=0)
        bucket[inizio_ora] += punto.valore_kwh
        conteggio[inizio_ora] += 1
        if punto.valore_kwh:
            non_nulli[inizio_ora] += 1

    for ora, quanti in conteggio.items():
        if quanti > 4 and non_nulli[ora] > 4:
            _LOGGER.warning(
                "Ora %s: %d intervalli di cui %d non nulli, sommati insieme. Attesi 4: "
                "succede al cambio ora, ma finora gli intervalli in eccesso erano a "
                "zero. Il totale di quell'ora potrebbe essere sovrastimato.",
                ora.isoformat(),
                quanti,
                non_nulli[ora],
            )

    return sorted(bucket.items())


async def async_import_curva(
    hass: HomeAssistant,
    pod: str,
    risultato: RisultatoLetture,
    nome_pod: str | None = None,
    distributor_display_name: str = "Contatore",
) -> date | None:
    """Importa i punti curva di un POD come external statistics.

    Fonde i nuovi dati con la serie già presente e RICALCOLA da zero tutte le
    somme progressive, invece di limitarsi ad accodare. Questo rende
    irrilevante l'ordine di inserimento: si possono importare mesi storici
    dopo aver già importato dati recenti (backfill all'indietro) senza che le
    somme risultino incoerenti - cosa impossibile accodando soltanto, perché
    la somma è cumulativa e i punti successivi manterrebbero valori sbagliati,
    producendo un salto artificiale nel grafico.

    Restituisce la data (locale) dell'ultimo punto della serie risultante,
    oppure None se non c'è nulla. Il chiamante la usa per aggiornare subito i
    sensori diagnostici: async_add_external_statistics accoda la scrittura al
    recorder, quindi rileggere il database subito dopo non mostrerebbe ancora
    i dati appena scritti.
    """
    if not risultato.punti:
        _LOGGER.debug("Nessun punto curva da importare per POD %s", pod)
        return None

    statistic_id = sanitize_statistic_id(pod)
    nuove_ore = dict(_aggrega_per_ora(risultato.punti))
    _LOGGER.debug(
        "POD %s: %d punti a 15 minuti aggregati in %d ore (%s -> %s)",
        pod,
        len(risultato.punti),
        len(nuove_ore),
        min(nuove_ore).isoformat() if nuove_ore else "-",
        max(nuove_ore).isoformat() if nuove_ore else "-",
    )

    nome = nome_pod or f"{distributor_display_name} {pod}"
    return await async_scrivi_serie_oraria(hass, pod, statistic_id, nome, nuove_ore)


async def async_get_ultima_data_disponibile(hass: HomeAssistant, pod: str) -> date | None:
    """Restituisce la data (senza ora) dell'ultimo punto curva effettivamente
    presente nelle external statistics per il POD, o None se non c'è ancora
    nessun dato importato."""
    return await async_ultima_data_disponibile(hass, sanitize_statistic_id(pod))
