"""Plugin distributore E-Distribuzione.

A differenza di Duereti/Unareti, protocollo completamente diverso (OAuth2+PKCE
con OTP via Salesforce, non il protocollo PCF): non usa pcf_common. Auth/API
sono state portate dalla bozza originale (auth.py, api.py) senza modifiche
alla logica.

STATO: auth.py/api.py/coordinator.py sono integrati e funzionanti (nella
misura in cui lo era la bozza originale - vedi i caveat in auth.py sui
regex di scraping non testati contro l'HTML reale). statistics.py NON è
ancora scritto: manca lo schema JSON confermato della curva di carico
(async_get_daily_load_profile/async_get_monthly_load_profile), quindi oggi
non c'è importazione nella Energy Dashboard per questo distributore, solo
i sensori diagnostici/di stato di sensor.py.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DISPLAY_NAME, PIVA
from .coordinator import EdistribuzioneCoordinator
from .sensor import build_edistribuzione_entities

__all__ = ["DISPLAY_NAME", "PIVA", "create_coordinator", "build_sensor_entities"]


def create_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> EdistribuzioneCoordinator:
    return EdistribuzioneCoordinator(hass, entry)


def build_sensor_entities(hass, coordinator: EdistribuzioneCoordinator, entry: ConfigEntry) -> list:
    return build_edistribuzione_entities(hass, coordinator)
