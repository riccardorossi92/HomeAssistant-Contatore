"""Plugin distributore Ireti (smartpod.ireti.it, gruppo Iren).

Protocollo diverso da tutti gli altri già supportati: REST + Bearer token
(Keycloak, password grant diretto - nessun OTP, nessuna sessione a
cookie/Aura come Areti). L'endpoint di misura
(/readings/exabeat/measures-loadprofiles, curva di carico a 15 minuti) è
CONFERMATO su dati reali il 17/09/2026 (issue #6, russomichele). Percorso
completo: login (auth.py) -> chiamate REST (api.py) -> curva di carico
importata come external statistics (statistics.py), guidato dal
coordinator (coordinator.py, finestra scorrevole - non un cursore/coda
come gli altri, vedi il suo docstring). Dettagli completi e "perché" in
documentation/protocols/ireti-protocol.md.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DISPLAY_NAME, PIVA
from .coordinator import IretiCoordinator
from .sensor import build_ireti_entities

__all__ = ["DISPLAY_NAME", "PIVA", "create_coordinator", "build_sensor_entities"]


def create_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> IretiCoordinator:
    return IretiCoordinator(hass, entry)


def build_sensor_entities(hass, coordinator: IretiCoordinator, entry: ConfigEntry) -> list:
    return build_ireti_entities(hass, coordinator)
