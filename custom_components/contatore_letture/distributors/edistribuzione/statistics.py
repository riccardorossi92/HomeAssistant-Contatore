"""Import delle curve E-Distribuzione come external statistics.

STUB DELIBERATO: a differenza di pcf_common/statistics.py (dove il formato
CSV di Duereti è "CONFERMATO via test reale", colonna per colonna), qui NON
conosciamo lo schema JSON esatto restituito da:

  - EdistribuzioneApiClient.async_get_daily_load_profile
  - EdistribuzioneApiClient.async_get_monthly_load_profile

api.py si limita a fare `payload.get("data", [])` senza che nessun
consumatore nel codice originale ne legga il contenuto (a differenza di
`reading`, il cui schema conosciamo per certo da sensor.py:
`{"publishedSlots": [{"magnitude", "slotId", "value"}, ...],
"publishedPowerPeaks": [...]}`- ma quello è il dato mensile cumulativo per
fascia, non la curva a intervalli che serve per un import stile Energy
Dashboard).

Finché non abbiamo un payload reale (anche un solo giorno basta), questo
modulo si limita a loggare la struttura ricevuta a livello DEBUG/INFO, per
farla emergere la prima volta che qualcuno lo esegue con dati veri, invece
di inventare nomi di campo e rischiare di importare numeri sbagliati in
modo silenzioso.

Una volta noto lo schema, questa funzione va sostituita con l'equivalente
di pcf_common/statistics.py: aggregazione oraria, gestione DST (se
applicabile - va verificato se e come E-Distribuzione flagga il cambio ora
nei suoi dati, non c'è ancora nessuna informazione al riguardo), merge con
la serie esistente, async_add_external_statistics.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_import_curva_giornaliera(
    hass: HomeAssistant, pod: str, dati_grezzi: list[dict[str, Any]]
) -> None:
    """Punto di aggancio per l'import della curva giornaliera.

    Oggi NON importa nulla: si limita a loggare la struttura del primo
    elemento ricevuto, cosi' la prima esecuzione reale rivela lo schema
    effettivo (nomi dei campi, formato del timestamp, unita' di misura).

    TODO: una volta noto lo schema (manda un payload di esempio, anche
    con valori anonimizzati/azzerati, basta la struttura), sostituire il
    corpo di questa funzione con la logica vera di aggregazione + import,
    speculare a pcf_common/statistics.py::async_import_curva.
    """
    if not dati_grezzi:
        _LOGGER.debug("Nessun dato curva ricevuto per POD %s: nulla da importare", pod)
        return

    _LOGGER.warning(
        "Import statistiche E-Distribuzione non ancora implementato (schema JSON "
        "della curva non confermato). Struttura del primo elemento ricevuto per "
        "il POD %s, utile per completare l'implementazione: %s",
        pod,
        dati_grezzi[0],
    )
