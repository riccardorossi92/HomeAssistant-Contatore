"""Helper per collegare un dispositivo al suo "padre" nel device registry.

Home Assistant 2026.8 ha deprecato DeviceInfo["via_device"] (la tupla di
identificatori) in favore di via_device_id (l'ID interno del device
padre), perche' gli identificatori non sono piu' univoci a livello
globale ma solo per config entry.

Non e' solo una deprecazione con scadenza lontana (2027.8): su Home
Assistant 2026.9 il platform sensor puo' trasformarla in un RuntimeError
invece che in un warning, quando la catena di chiamate non permette a
Core di attribuire la chiamata all'integrazione custom - in quel caso le
entita' NON vengono aggiunte affatto. E' gia' successo ad altre
integrazioni (huawei_solar, aiohomematic) con HA 2026.9.

via_device_id pero' non esiste prima di HA 2026.8, e questa integrazione
supporta da 2025.4 in su: usarlo incondizionatamente taglierebbe fuori
tutti gli utenti su versioni precedenti. Quindi si sceglie a runtime in
base alla versione di Home Assistant, e il minimo dichiarato resta 2025.4.
"""
from __future__ import annotations

import logging

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

_LOGGER = logging.getLogger(__name__)

# Prima versione in cui DeviceInfo accetta via_device_id.
_VERSIONE_VIA_DEVICE_ID = (2026, 8)


def supporta_via_device_id() -> bool:
    """True se questa versione di HA accetta via_device_id in DeviceInfo.

    In caso di dubbio (versione non interpretabile) ritorna False: si
    ricade su via_device, che sulle versioni vecchie e' l'unica cosa che
    funziona, e su quelle nuove produce al massimo un warning.
    """
    try:
        maggiore, minore = HA_VERSION.split(".")[:2]
        return (int(maggiore), int(minore)) >= _VERSIONE_VIA_DEVICE_ID
    except (AttributeError, ValueError):
        _LOGGER.debug("Versione di Home Assistant non interpretabile: %r", HA_VERSION)
        return False


def assicura_dispositivo_padre(
    hass: HomeAssistant, entry_id: str, device_info_padre: dict
) -> str | None:
    """Registra (o recupera) il dispositivo padre e ne ritorna l'ID interno.

    Va chiamata PRIMA di costruire le entita' figlie: al primo avvio il
    padre non esiste ancora nel registry, perche' normalmente lo creano
    le entita' stesse quando vengono aggiunte - quindi cercarlo e basta
    ritornerebbe None proprio quando serve.

    Ritorna None se questa versione di HA non usa via_device_id (nel qual
    caso il chiamante ricade su via_device) o se la registrazione non
    riesce: un fallimento qui non deve impedire la creazione delle
    entita', al massimo si perde il collegamento gerarchico nella UI.
    """
    if not supporta_via_device_id():
        return None
    try:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(config_entry_id=entry_id, **device_info_padre)
        return device.id
    except Exception:  # noqa: BLE001 - vedi docstring: non deve mai bloccare
        _LOGGER.exception(
            "Impossibile registrare il dispositivo padre: i dispositivi per POD "
            "non verranno collegati gerarchicamente, ma le entita' funzionano"
        )
        return None


def collega_al_padre(
    device_info: dict,
    identificatori_padre: set[tuple[str, str]],
    id_padre: str | None,
) -> dict:
    """Aggiunge a device_info il collegamento al dispositivo padre.

    Usa via_device_id se disponibile (HA 2026.8+ e padre registrato),
    altrimenti via_device. Modifica e ritorna lo stesso dizionario.
    """
    if id_padre is not None:
        device_info["via_device_id"] = id_padre
    else:
        device_info["via_device"] = next(iter(identificatori_padre))
    return device_info
