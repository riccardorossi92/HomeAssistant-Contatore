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

sampleValues: un valore ogni 15 minuti, indice 0 = 00:00-00:15 locale,
indice 95 = 23:45-00:00. Si assume siano kWh per intervallo (somma
giornaliera ~1.9 nell'esempio reale, plausibile per un consumo di base -
NON confermato al 100%, potrebbe trattarsi di potenza media in kW: se
emerge il contrario, l'unico punto da correggere è qui, moltiplicando per
0.25 - l'import è idempotente, ricalcola sempre la somma progressiva da
zero, quindi correggerlo in futuro non lascia dati incoerenti indietro).

energyType: solo "A1" osservato finora (energia attiva). Si importano
SOLO gli elementi con energyType "A1": un POD "fasce"/"mono orario" (mai
visto, solo ipotizzato dal bundle JS) potrebbe restituire più elementi
per giorno con energyType diversi per fascia, nel qual caso filtrare solo
"A1" sotto-conterebbe il consumo invece di sommare valori eterogenei per
errore - preferibile importare un sottoinsieme sicuramente corretto che
sommare alla cieca. Vedi "Ipotesi da verificare" in
documentation/protocols/ireti-protocol.md.

Fuso orario: a differenza di Areti, 'loadProfileDate' porta un offset UTC
esplicito (qui "+0200") - ma non ci si affida a quell'offset per ogni
singolo campione (un giorno di cambio ora avrebbe un solo offset in testa
per tutta la giornata, sbagliato per la parte di giorno dopo il cambio):
si segue lo stesso approccio di Areti, si ricostruisce l'ora locale a
partire dall'indice (quarto d'ora) e si converte in UTC con
dt_util.as_utc, lasciando che sia il database dei fusi orari di Home
Assistant a gestire il cambio ora.

NON VERIFICATO: comportamento nei giorni di cambio ora legale (92
campioni invece di 96, secondo il codice del bundle JS - la cattura
reale disponibile, agosto 2026, non ne conteneva nessuno). Stesso identico
avvertimento del modulo equivalente di Areti.
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

# Solo l'energia attiva è consumo importabile con fiducia - vedi docstring.
_ENERGY_TYPE_ATTIVA = "A1"

_MINUTI_PER_CAMPIONE = 15
_CAMPIONI_ATTESI = (92, 96)  # 96 normale, 92 nel giorno di cambio ora legale


def _ora_da_indice(indice: int) -> time:
    """Indice di campione (0-95, quarti d'ora dalla mezzanotte) -> ora
    locale del suo inizio, es. indice 2 -> 00:30:00."""
    minuti_totali = indice * _MINUTI_PER_CAMPIONE
    return time(hour=(minuti_totali // 60) % 24, minute=minuti_totali % 60)


def _aggrega_per_ora(load_profiles: list[dict[str, Any]]) -> list[tuple[datetime, float]]:
    """Aggrega i campioni a 15 minuti (locali) di più giorni in bucket orari (UTC).

    Elementi con campi mancanti/non parsabili, o con energyType diverso da
    "A1", vengono scartati con un log invece di far fallire l'intero
    import: un giorno corrotto o con un tipo inatteso non deve perdere il
    resto della finestra richiesta."""
    bucket: dict[datetime, float] = defaultdict(float)

    for elemento in load_profiles:
        energy_type = elemento.get("energyType")
        if energy_type != _ENERGY_TYPE_ATTIVA:
            _LOGGER.info(
                "loadProfile con energyType %r (atteso %r) scartato: %r",
                energy_type, _ENERGY_TYPE_ATTIVA, elemento.get("loadProfileDate"),
            )
            continue

        try:
            giorno = datetime.strptime(elemento["loadProfileDate"][:10], "%d/%m/%Y").date()
            campioni = elemento["sampleValues"]
        except (KeyError, TypeError, ValueError) as exc:
            _LOGGER.warning("loadProfile non valido, scartato: %r (%s)", elemento, exc)
            continue

        if len(campioni) not in _CAMPIONI_ATTESI:
            _LOGGER.warning(
                "loadProfile del %s ha %d campioni (attesi %s) - schema cambiato "
                "rispetto a quello confermato il 17/09/2026? Giorno scartato.",
                giorno, len(campioni), _CAMPIONI_ATTESI,
            )
            continue

        for indice, valore in enumerate(campioni):
            if valore is None:
                continue
            try:
                valore_kwh = float(valore)
            except (TypeError, ValueError):
                continue
            locale = datetime.combine(giorno, _ora_da_indice(indice)).replace(
                tzinfo=dt_util.DEFAULT_TIME_ZONE
            )
            ts_utc = dt_util.as_utc(locale)
            inizio_ora = ts_utc.replace(minute=0, second=0, microsecond=0)
            bucket[inizio_ora] += valore_kwh

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
