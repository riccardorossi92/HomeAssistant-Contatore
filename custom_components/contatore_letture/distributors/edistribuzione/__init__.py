"""Plugin distributore E-Distribuzione.

A differenza di Duereti/Unareti, protocollo completamente diverso (OAuth2+PKCE
con OTP via Salesforce, non il protocollo PCF): non usa pcf_common.

Percorso completo funzionante: login email/password + OTP (auth.py) ->
recupero POD e curva di carico giornaliera (api.py) -> import come external
statistics nella Energy Dashboard (statistics.py), guidato dal coordinator
(coordinator.py, con coda di retry per-POD). Caveat storico ancora valido:
auth.py fa scraping di pagine Salesforce con dei regex fragili, che si
rompono se Enel cambia il markup - vedi i commenti lì e
scripts/verify_edistribuzione_login.py per verificarli contro l'HTML reale.
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
