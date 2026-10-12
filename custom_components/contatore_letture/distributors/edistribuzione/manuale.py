"""Config entry E-Distribuzione in modalita' "caricamento manuale".

Nessun login e nessun download periodico: l'entry esiste solo per sapere a
quale POD appartengono i file caricati dalle opzioni. Niente
DataUpdateCoordinator, sensori o dispositivi: i dati finiscono nelle
external statistics, le stesse della modalita' automatica.
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_MODALITA, CONF_PODS, FUSO_PORTALE, MODALITA_MANUALE
from .csv_curve import CurvaDaCsv, leggi_csv_curva
from .statistics import async_import_ore

_LOGGER = logging.getLogger(__name__)


def e_manuale(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_MODALITA) == MODALITA_MANUALE


class EdistribuzioneManuale:
    """Stato di una entry manuale in hass.data[DOMAIN]."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.pods: list[str] = list(entry.data[CONF_PODS])

    async def async_importa_csv(self, contenuto: bytes) -> CurvaDaCsv:
        """Legge l'export del portale e lo importa sul POD della entry.

        Solleva csv_curve.CsvCurveError se il file non e' riconosciuto."""
        curva = await self.hass.async_add_executor_job(
            leggi_csv_curva, contenuto, ZoneInfo(FUSO_PORTALE)
        )
        pod = self.pods[0]
        for avviso in curva.avvisi:
            _LOGGER.warning("CSV E-Distribuzione per %s: %s", pod, avviso)
        await async_import_ore(self.hass, pod, curva.ore)
        _LOGGER.info(
            "CSV E-Distribuzione importato per %s: %d giorni (%s - %s), %.3f kWh",
            pod,
            len(curva.giorni),
            curva.giorni[0],
            curva.giorni[-1],
            curva.kwh_totali,
        )
        return curva
