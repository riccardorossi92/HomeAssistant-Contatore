"""Platform sensor di contatore_letture.

Un solo sensor.py per tutti i distributori: per quelli di tipo "pcf"
(Duereti/Unareti) le entità sono costruite da pcf_common tramite il modulo
del distributore specifico (che sa quale display_name usare). Per
E-Distribuzione (ancora uno stub, nessun coordinator reale) non viene
creata nessuna entità per ora.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .distributors import DISTRIBUTOR_REGISTRY


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    distributor_key = entry.data["distributor"]
    info = DISTRIBUTOR_REGISTRY[distributor_key]

    coordinator = hass.data[DOMAIN][entry.entry_id]
    entità = info["module"].build_sensor_entities(coordinator, entry)
    async_add_entities(entità)
