"""Plugin distributore Reti Valtellina Valchiavenna (RE.V.V.).

Sottile: tutta la logica reale vive in pcf_common, parametrizzata su
BASE_URL/DISPLAY_NAME. Il manuale "API per Estrazione Curve e Letture"
di RE.V.V. (scaricato da downloadFileTemplate.action?codice=MANUALE_RICH_API,
stesso percorso di Duereti) descrive lo stesso protocollo di Duereti/Unareti:
requestToken -> requestExport -> requestResult, stessi campi, stessi codici
di errore, stesso zip Base64 (CSV per le curve, XLSX per le letture).
Non ancora provato con credenziali reali: se RE.V.V. diverge, l'override va
qui, senza toccare pcf_common ne' gli altri moduli PCF.
Il manuale e' identico parola per parola a quello di RetiPiù, a parte HOST_NAME.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .pcf_common import config_flow_helpers
from .pcf_common.coordinator import PcfCoordinator
from .pcf_common.sensor import build_pcf_entities

DISPLAY_NAME = "Reti Valtellina Valchiavenna"
PIVA = "01017590140"  # confermata via scheda operatore ARERA (Id operatore 28064, gruppo Acinque)
BASE_URL = "https://areaclienti.valtellinarevv.it/ClientiRvvWeb/public/misure"
PORTAL_URL = "https://areaclienti.valtellinarevv.it/ClientiRvvWeb"

REQUIRED_INFO = [
    "Client ID e Secret ID del Portale Clienti Finali (PCF) Reti Valtellina Valchiavenna",
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
