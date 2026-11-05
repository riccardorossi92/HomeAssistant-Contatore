"""Plugin distributore Edyna (portaledistributore.edyna.net, Alto Adige /
Südtirol, gruppo Alperia).

Portale Terranova PUF (ASP.NET WebForms, nessun JSON): login con nome utente e
password (nessun OTP), poi navigazione a postback Utenze -> Curve -> mese per
leggere la curva oraria, importata come external statistics. Login e pagine
verificati su catture reali del 05/11/2026; la catena di postback intermedia
è ricostruita dagli id dei controlli e non ancora provata dal vivo.
Percorso: auth.py -> api.py (+ parse.py) -> statistics.py, guidato da
coordinator.py (una lettura al giorno, mese per mese). Dettagli e "perché" in
documentation/protocols/edyna-protocol.md.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DISPLAY_NAME, PIVA
from .coordinator import EdynaCoordinator
from .sensor import build_edyna_entities

__all__ = ["DISPLAY_NAME", "PIVA", "create_coordinator", "build_sensor_entities"]


def create_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> EdynaCoordinator:
    return EdynaCoordinator(hass, entry)


def build_sensor_entities(hass, coordinator: EdynaCoordinator, entry: ConfigEntry) -> list:
    return build_edyna_entities(hass, coordinator)
