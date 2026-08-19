"""Plugin distributore Duereti.

Sottile: tutta la logica reale vive in pcf_common, parametrizzata su
BASE_URL/DISPLAY_NAME. Se domani Duereti diverge da Unareti su qualcosa
(endpoint diverso, formato diverso, quirk specifico), l'override va qui,
senza toccare pcf_common ne' unareti.py.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .pcf_common import config_flow_helpers
from .pcf_common.coordinator import PcfCoordinator
from .pcf_common.sensor import build_pcf_entities

DISPLAY_NAME = "Duereti"
PIVA = "13632560960"  # verificato via HAR ARERA il 19/08/2026 (Vimodrone)
BASE_URL = "https://areaclienti.duereti.it/ClientiDueRetiWeb/public/misure"
PORTAL_URL = "https://areaclienti.duereti.it/ClientiDueRetiWeb"

REQUIRED_INFO = [
    "Client ID e Secret ID del Portale Clienti Finali (PCF) Duereti",
    "Codice POD per ciascun punto di prelievo",
    "Dato fiscale associato (codice fiscale o P.IVA dell'intestatario della fornitura)",
]


def create_coordinator(
    hass: HomeAssistant, entry: ConfigEntry, client_id: str, secret_id: str, pods: list[dict]
) -> PcfCoordinator:
    return PcfCoordinator(
        hass,
        entry,
        client_id=client_id,
        secret_id=secret_id,
        pods=pods,
        base_url=BASE_URL,
        display_name=DISPLAY_NAME,
    )


async def async_valida_credenziali(hass, client_id: str, secret_id: str) -> str | None:
    return await config_flow_helpers.async_valida_credenziali(hass, client_id, secret_id, BASE_URL)


async def async_valida_pod(hass, client_id: str, secret_id: str, pod: str, df: str):
    return await config_flow_helpers.async_valida_pod(hass, client_id, secret_id, pod, df, BASE_URL)


def build_sensor_entities(coordinator, entry: ConfigEntry) -> list:
    return build_pcf_entities(coordinator, entry, DISPLAY_NAME)
