"""Diagnostica scaricabile dalla pagina dell'integrazione.

Serve a chi apre una issue: al posto di log interi o di
.storage/core.config_entries basta allegare questo file, che contiene
configurazione e stato del coordinator senza credenziali.

Restano i codici POD (servono a ricollegare la segnalazione ai log, e
compaiono gia' nel titolo della voce); spariscono password, email, token,
Client ID/Secret ID, il dato fiscale associato ai POD (codice fiscale o
partita IVA) e i ticket delle estrazioni in corso.
"""
from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN

DA_OSCURARE = {
    "access_token",
    "client_id",
    "df",
    "email",
    "id_token",
    "password",
    "pending_ticket",
    "refresh_token",
    "secret_id",
    "ticket",
    "token",
    "username",
}

# Un messaggio d'errore puo' riportare un token (es. il 409 di requestToken
# dei distributori PCF: "... token: XMzKH...") o un'email.
_RE_TOKEN_IN_TESTO = re.compile(r"(?i)\b(token|ticket)(\s*[:=]\s*)\S+")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _oscura_testo(testo: str) -> str:
    testo = _RE_TOKEN_IN_TESTO.sub(r"\1\2**REDACTED**", testo)
    return _RE_EMAIL.sub("**REDACTED**", testo)


def _stato_coordinator(coordinator: DataUpdateCoordinator) -> dict[str, Any]:
    ultimo_errore = coordinator.last_exception
    return {
        "tipo": type(coordinator).__name__,
        "ultimo_aggiornamento_riuscito": coordinator.last_update_success,
        "intervallo_aggiornamento": str(coordinator.update_interval),
        "ultimo_errore": (
            None
            if ultimo_errore is None
            else f"{type(ultimo_errore).__name__}: {_oscura_testo(str(ultimo_errore))}"
        ),
        "dati": async_redact_data(coordinator.data, DA_OSCURARE),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Configurazione e stato di una voce, senza credenziali."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    return {
        "voce": {
            "titolo": entry.title,
            "versione": entry.version,
            "dati": async_redact_data(dict(entry.data), DA_OSCURARE),
            "opzioni": async_redact_data(dict(entry.options), DA_OSCURARE),
        },
        "coordinator": (
            _stato_coordinator(coordinator)
            if isinstance(coordinator, DataUpdateCoordinator)
            else None
        ),
    }
