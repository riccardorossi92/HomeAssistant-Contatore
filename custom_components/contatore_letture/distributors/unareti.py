"""Plugin distributore Unareti.

Sottile: tutta la logica reale vive in pcf_common, parametrizzata su
BASE_URL/DISPLAY_NAME. Oggi il protocollo e' identico a Duereti (verificato
riga per riga sui due manuali/integrazioni originali), ma questo modulo
resta un file a se' stante apposta: se in futuro Unareti diverge (nuovo
endpoint, formato diverso, comportamento WAF diverso da quello osservato
su Duereti - vedi note in pcf_common/api.py), l'override va qui, senza
toccare pcf_common ne' duereti.py.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .pcf_common import config_flow_helpers
from .pcf_common.coordinator import PcfCoordinator
from .pcf_common.sensor import build_pcf_entities

DISPLAY_NAME = "Unareti"
PIVA = "12883450152"  # confermata via scheda operatore ARERA (Id operatore 1247, gruppo A2A)
BASE_URL = "https://areaclienti.unareti.it/ClientiWeb/public/misure"
PORTAL_URL = "https://areaclienti.unareti.it/ClientiWeb"

REQUIRED_INFO = [
    "Client ID e Secret ID del Portale Clienti Finali (PCF) Unareti",
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


def build_sensor_entities(hass, coordinator, entry: ConfigEntry) -> list:
    return build_pcf_entities(hass, coordinator, entry, DISPLAY_NAME)
