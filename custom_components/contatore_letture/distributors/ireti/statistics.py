"""Import della curva di carico Ireti come external statistics.

Schema di un elemento di 'loadProfiles' (dentro la risposta di
IretiApiClient.async_get_measures_loadprofiles) CONFERMATO su dati reali
il 17/09/2026 (issue #6, russomichele):

    {
      "loadProfileDate": "20/08/2026 00:00:00 +0200",
      "serialNumber": "...",
      "timeType": "",
      "energyType": "A1",
      "measType": "0",
      "sampleValues": [0.023, 0.022, ... 96 valori totali]
    }

sampleValues: un valore ogni 15 minuti in kWh per intervallo, a partire
dalla mezzanotte locale - unità confermata dalla issue #10 (totali mensili
importati uguali al kWh alle bollette per 5 mesi su 5).

energyType: si importano SOLO gli elementi "A1" (energia attiva). Ogni
giorno arriva anche un elemento "R1" (reattiva, issue #10), che va
scartato; un POD "fasce"/"mono orario" (mai visto) potrebbe inoltre
restituire più elementi per giorno con energyType diversi per fascia:
meglio importare un sottoinsieme sicuramente corretto che sommare valori
eterogenei. Vedi "Cosa resta aperto" in
documentation/protocols/ireti-protocol.md.

Fuso orario e cambio ora - verificati su dati reali (issue #10):

- l'indice è il tempo TRASCORSO dalla mezzanotte locale, non l'ora sul
  quadrante: il campione i inizia a mezzanotte (convertita in UTC) +
  i x 15 minuti. Nel giorno da 23 ore (29/03/2026) arriva un solo
  elemento da 92 campioni; ricostruire l'ora locale dall'indice
  spostava tutto di un'ora dalle 03:00 in poi;
- nel giorno da 25 ore (26/10/2025) arrivano DUE elementi A1 con la
  stessa loadProfileDate: uno da 12 campioni (00:00-02:59 ora legale) e
  uno da 96 con i primi 8 a null (dalle 02:00 ora solare in poi). Uniti
  - prima quello senza null in testa, poi l'altro privato dei null
  iniziali - danno i 100 campioni della giornata.

Il numero di campioni atteso per un giorno (92/96/100) si ricava dal
database dei fusi di Home Assistant; un giorno che non torna viene
scartato con un avviso invece di importare dati disallineati.
"""
from __future__ import annotations

import logging
from collections import defaultdict
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

# Solo l'energia attiva è consumo importabile con fiducia - vedi docstring.
_ENERGY_TYPE_ATTIVA = "A1"

_DURATA_CAMPIONE = timedelta(minutes=15)


def _mezzanotte_utc(giorno: date) -> datetime:
    return dt_util.as_utc(
        datetime.combine(giorno, time.min).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    )


def _campioni_attesi(giorno: date) -> int:
    """Quarti d'ora nella giornata locale: 96, 92 o 100 nei giorni di
    cambio ora."""
    durata = _mezzanotte_utc(giorno + timedelta(days=1)) - _mezzanotte_utc(giorno)
    return durata // _DURATA_CAMPIONE


def _unisci_elementi_del_giorno(elementi: list[list[Any]]) -> list[Any]:
    """Più elementi A1 con la stessa data (giorno da 25 ore, vedi
    docstring) -> un'unica sequenza di campioni in ordine di tempo.

    Il portale non li manda in ordine: va prima quello che non comincia
    con null, poi gli altri privati dei null iniziali (la parte di giorno
    già coperta dal primo). Con un solo elemento i null restano dove sono:
    sono buchi del dato, non sovrapposizioni."""
    if len(elementi) == 1:
        return elementi[0]
    ordinati = sorted(elementi, key=lambda campioni: bool(campioni) and campioni[0] is None)
    uniti = list(ordinati[0])
    for campioni in ordinati[1:]:
        indice = 0
        while indice < len(campioni) and campioni[indice] is None:
            indice += 1
        uniti.extend(campioni[indice:])
    return uniti


def _aggrega_per_ora(load_profiles: list[dict[str, Any]]) -> list[tuple[datetime, float]]:
    """Aggrega i campioni a 15 minuti di più giorni in bucket orari (UTC).

    Elementi con campi mancanti/non parsabili, o con energyType diverso da
    "A1", vengono scartati con un log invece di far fallire l'intero
    import: un giorno corrotto o con un tipo inatteso non deve perdere il
    resto della finestra richiesta."""
    per_giorno: dict[date, list[list[Any]]] = defaultdict(list)

    for elemento in load_profiles:
        energy_type = elemento.get("energyType")
        if energy_type != _ENERGY_TYPE_ATTIVA:
            _LOGGER.debug(
                "loadProfile con energyType %r (atteso %r) scartato: %r",
                energy_type, _ENERGY_TYPE_ATTIVA, elemento.get("loadProfileDate"),
            )
            continue

        try:
            giorno = datetime.strptime(elemento["loadProfileDate"][:10], "%d/%m/%Y").date()
            campioni = list(elemento["sampleValues"])
        except (KeyError, TypeError, ValueError) as exc:
            _LOGGER.warning("loadProfile non valido, scartato: %r (%s)", elemento, exc)
            continue
        per_giorno[giorno].append(campioni)

    bucket: dict[datetime, float] = defaultdict(float)
    for giorno in sorted(per_giorno):
        elementi = per_giorno[giorno]
        campioni = _unisci_elementi_del_giorno(elementi)
        attesi = _campioni_attesi(giorno)
        if len(campioni) != attesi:
            _LOGGER.warning(
                "loadProfile del %s: %d campioni in %d elementi A1 (attesi %d) - "
                "schema cambiato rispetto a quello confermato? Giorno scartato.",
                giorno, len(campioni), len(elementi), attesi,
            )
            continue

        mezzanotte = _mezzanotte_utc(giorno)
        for indice, valore in enumerate(campioni):
            if valore is None:
                continue
            try:
                valore_kwh = float(valore)
            except (TypeError, ValueError):
                continue
            inizio = mezzanotte + indice * _DURATA_CAMPIONE
            bucket[inizio.replace(minute=0, second=0, microsecond=0)] += valore_kwh

    return sorted(bucket.items())


async def async_import_curva_giorni(
    hass: HomeAssistant, pod: str, load_profiles: list[dict[str, Any]]
) -> date | None:
    """Importa i campioni a 15 minuti di più giorni (una finestra
    scorrevole, non un giorno solo) come external statistics, aggregati in
    bucket orari.

    Stessa logica di fusione/ricalcolo cumulativo di pcf_common/
    edistribuzione/areti: rilegge la serie esistente, la fonde con i nuovi
    dati (quelli nuovi hanno la precedenza in caso di sovrapposizione - è
    così che una finestra scorrevole può ripassare più volte sullo stesso
    giorno senza problemi) e ricalcola tutte le somme progressive da zero.

    Restituisce la data locale dell'ultimo punto della serie risultante,
    o None se non c'è nulla da importare.
    """
    if not load_profiles:
        _LOGGER.debug("Nessun dato curva da importare per POD %s", pod)
        return None

    statistic_id = sanitize_statistic_id(pod)
    nuove_ore = dict(_aggrega_per_ora(load_profiles))

    if not nuove_ore:
        _LOGGER.warning(
            "POD %s: nessun campione valido trovato in loadProfiles (schema "
            "cambiato rispetto a quello confermato il 17/09/2026?). "
            "Risposta grezza: %r",
            pod,
            load_profiles,
        )
        return None

    return await async_scrivi_serie_oraria(hass, pod, statistic_id, f"Ireti {pod}", nuove_ore)


async def async_get_ultima_data_disponibile(hass: HomeAssistant, pod: str) -> date | None:
    """Ultima data (locale) effettivamente presente nelle external
    statistics per il POD, o None se non c'è ancora nessun dato importato."""
    return await async_ultima_data_disponibile(hass, sanitize_statistic_id(pod))
