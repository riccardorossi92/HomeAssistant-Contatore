"""Plugin distributore Areti (gruppo ACEA).

Protocollo completamente diverso da PCF (Duereti/Unareti) e da OAuth2+OTP
(E-Distribuzione): sessione a cookie su un portale Salesforce Experience
Cloud/Aura, login via una vecchia pagina Visualforce/JSF, NESSUN OTP
osservato. Percorso completo verificato su dati reali il 04/09/2026:
login (auth.py) -> catena di chiamate Aura (api.py) -> curva di carico a
15 minuti importata come external statistics (statistics.py), guidato dal
coordinator (coordinator.py, cursore mensile persistito per POD - non un
sistema a coda/ritardo come gli altri, vedi il suo docstring). Dettagli
completi e "perché" in documentation/areti-protocol.md.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DISPLAY_NAME, PIVA
from .coordinator import AretiCoordinator
from .sensor import build_areti_entities

__all__ = ["DISPLAY_NAME", "PIVA", "create_coordinator", "build_sensor_entities"]


def create_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> AretiCoordinator:
    return AretiCoordinator(hass, entry)


def build_sensor_entities(hass, coordinator: AretiCoordinator, entry: ConfigEntry) -> list:
    return build_areti_entities(hass, coordinator)
