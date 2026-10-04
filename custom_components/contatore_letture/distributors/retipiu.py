"""Plugin distributore RetiPiù.

Sottile: tutta la logica reale vive in pcf_common, parametrizzata su
BASE_URL/DISPLAY_NAME. Il manuale "API per Estrazione Curve e Letture"
di RetiPiù (scaricato da downloadFileTemplate.action?codice=MANUALE_RICH_API,
stesso percorso di Duereti) descrive lo stesso protocollo di Duereti/Unareti:
requestToken -> requestExport -> requestResult, stessi campi, stessi codici
di errore, stesso zip Base64 (CSV per le curve, XLSX per le letture).
Non ancora provato con credenziali reali: se RetiPiù diverge, l'override va
qui, senza toccare pcf_common ne' duereti.py/unareti.py.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .pcf_common import config_flow_helpers
from .pcf_common.coordinator import PcfCoordinator
from .pcf_common.sensor import build_pcf_entities

DISPLAY_NAME = "RetiPiù"
PIVA = "04152790962"  # confermata via scheda operatore ARERA (Id operatore 353, gruppo A2A)
BASE_URL = "https://areaclienti.retipiu.it/ClientiRPiuWeb/public/misure"
PORTAL_URL = "https://areaclienti.retipiu.it/ClientiRPiuWeb"

REQUIRED_INFO = [
    "Client ID e Secret ID del Portale Clienti Finali (PCF) RetiPiù",
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
