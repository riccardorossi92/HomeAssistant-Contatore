"""Plugin distributore SET Distribuzione (myset.setdistribuzione.it, gruppo
Dolomiti Energia).

Login Azure AD B2C (Authorization Code + PKCE, nessun OTP) -> API REST con
Bearer token -> curva oraria (con i quarti d'ora) di un giorno per
richiesta, importata come external statistics. Verificato su una cattura
reale di un account con POD attivo (13/10/2026). Percorso: auth.py ->
api.py -> statistics.py, guidato da coordinator.py (coda giornaliera
per-POD come edistribuzione/ireti). Dettagli e "perché" in
documentation/protocols/set-distribuzione-protocol.md.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DISPLAY_NAME, PIVA
from .coordinator import SetDistribuzioneCoordinator
from .sensor import build_setdistribuzione_entities

__all__ = ["DISPLAY_NAME", "PIVA", "create_coordinator", "build_sensor_entities"]


def create_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> SetDistribuzioneCoordinator:
    return SetDistribuzioneCoordinator(hass, entry)


def build_sensor_entities(
    hass, coordinator: SetDistribuzioneCoordinator, entry: ConfigEntry
) -> list:
    return build_setdistribuzione_entities(hass, coordinator)
