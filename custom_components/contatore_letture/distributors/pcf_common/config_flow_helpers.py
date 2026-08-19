"""Funzioni di validazione condivise per il config/options flow PCF
(Duereti/Unareti): verifica credenziali, verifica POD, controllo duplicati.

Parametrizzate su base_url e display_name cosi' lo stesso codice serve
entrambi i distributori: vedi come vengono chiamate da config_flow.py
dell'orchestratore principale.
"""
from __future__ import annotations

import logging

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    PcfApiClient,
    PcfApiError,
    PcfAuthError,
    PcfRateLimitError,
    PcfValidationError,
)
from ...const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def pod_gia_configurato(
    hass,
    pod: str,
    pods_gia_in_flow: list[dict] | None = None,
    escludi_entry_id: str | None = None,
) -> str | None:
    """Verifica se il POD e' gia' configurato in un'altra istanza
    dell'integrazione (o gia' aggiunto nel flow corrente prima di salvare).

    Il controllo e' globale su tutte le entry di contatore_letture,
    indipendentemente dal distributore: un POD e' univoco a livello
    nazionale, quindi non ha senso permettere lo stesso POD sotto due
    distributori diversi. Restituisce il titolo della config entry in
    conflitto, o None se il POD e' libero.
    """
    pod_norm = pod.strip().upper()

    for p in pods_gia_in_flow or []:
        if p["pod"].strip().upper() == pod_norm:
            return "questa stessa configurazione"

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == escludi_entry_id:
            continue
        for p in entry.data.get("pods", []):
            if p["pod"].strip().upper() == pod_norm:
                return entry.title

    return None


async def async_valida_credenziali(
    hass, client_id: str, secret_id: str, base_url: str
) -> str | None:
    """Prova ad autenticarsi con le credenziali fornite.

    Restituisce None se valide, altrimenti la chiave di errore da mostrare
    ('invalid_auth' o 'cannot_connect').
    """
    session = async_get_clientsession(hass)
    client = PcfApiClient(session, client_id, secret_id, base_url)
    try:
        await client.async_validate_credentials()
    except PcfAuthError:
        return "invalid_auth"
    except PcfApiError:
        return "cannot_connect"
    return None


async def async_valida_pod(
    hass, client_id: str, secret_id: str, pod: str, df: str, base_url: str
):
    """Verifica che la coppia POD/dato fiscale sia accettata dal distributore.

    Restituisce (chiave_errore, ticket):

    - ("pod_non_valido", None) se il distributore risponde che non ci sono
      POD validi;
    - (None, ticket) se la coppia e' valida. Il ticket corrisponde a un job
      realmente accodato: va salvato come pendente, cosi' il primo ciclo lo
      riprende invece di accodarne un altro per lo stesso giorno.
    - (None, None) se la verifica non e' stata possibile (rete non
      disponibile, limite di richieste raggiunto). In quel caso NON
      blocchiamo la configurazione: sarebbe assurdo impedire di configurare
      l'integrazione perche' il distributore e' momentaneamente
      irraggiungibile, e l'eventuale errore emergera' comunque al primo
      aggiornamento.
    """
    session = async_get_clientsession(hass)
    client = PcfApiClient(session, client_id, secret_id, base_url)
    try:
        ticket = await client.async_valida_pod(pod, df)
    except PcfValidationError as err:
        # 400: se il messaggio parla di POD/PDR la coppia e' sbagliata;
        # qualunque altro 400 non riguarda il POD, quindi non blocchiamo.
        if "pod" in str(err).lower() or "pdr" in str(err).lower():
            return "pod_non_valido", None
        _LOGGER.warning("Verifica del POD non conclusiva: %s", err)
        return None, None
    except PcfRateLimitError as err:
        _LOGGER.warning(
            "Limite di richieste raggiunto durante la verifica del POD %s: la "
            "configurazione prosegue senza verifica (%s)",
            pod,
            err,
        )
        return None, None
    except PcfApiError as err:
        _LOGGER.warning(
            "Impossibile verificare il POD %s ora (%s): la configurazione prosegue, "
            "un eventuale errore emergera' al primo aggiornamento",
            pod,
            err,
        )
        return None, None
    return None, ticket
