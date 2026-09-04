"""Integrazione contatore_letture.

Meta-integrazione che individua il distributore elettrico competente per
un comune (via lookup live su ARERA) e delega alla logica del distributore
specifico. Duereti e Unareti condividono il coordinator PCF (pcf_common);
E-Distribuzione ha un coordinator proprio (protocollo diverso, OAuth2+OTP
invece di Client ID/Secret ID) e Areti un altro ancora (sessione a cookie,
nessun OTP, cursore mensile invece di coda giornaliera) - entrambi
implementano la stessa azione 'recupera_storico' con firma compatibile
(pod opzionale, non solo per l'intera configurazione come i PCF).
"""
from __future__ import annotations

import logging
from datetime import date

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import CONF_CLIENT_ID, CONF_PODS, CONF_SECRET_ID, DOMAIN
from .distributors import DISTRIBUTOR_REGISTRY
from .distributors.areti.coordinator import AretiCoordinator
from .distributors.edistribuzione.coordinator import EdistribuzioneCoordinator
from .distributors.pcf_common.coordinator import PcfCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

SERVICE_RECUPERA_TICKET = "recupera_ticket"
SERVICE_RECUPERA_STORICO = "recupera_storico"

SCHEMA_RECUPERA_STORICO = vol.Schema({
    vol.Required("data_da"): cv.date,
    vol.Required("data_a"): cv.date,
    vol.Required("device_id"): cv.string,
})

SCHEMA_RECUPERA_TICKET = vol.Schema({
    vol.Required("ticket"): cv.string,
    vol.Optional("data_da"): cv.date,
    vol.Optional("data_a"): cv.date,
    vol.Optional("entry_id"): cv.string,
})


def _trova_coordinator(
    hass: HomeAssistant, entry_id: str | None, tipi: tuple[type, ...]
):
    """Individua il coordinator su cui agire, tra i tipi accettati.

    Usata da 'recupera_ticket' (resta su entry_id testuale: un solo
    coordinator PCF per entry, non c'e' granularita' di dispositivo da
    scegliere)."""
    coordinators: dict = {
        eid: c for eid, c in hass.data.get(DOMAIN, {}).items() if isinstance(c, tipi)
    }
    if not coordinators:
        nomi = "/".join(t.__name__ for t in tipi)
        raise HomeAssistantError(f"Nessuna istanza {nomi} configurata in contatore_letture.")

    if entry_id:
        if entry_id not in coordinators:
            raise HomeAssistantError(
                f"Nessuna istanza con entry_id '{entry_id}'. Disponibili: {list(coordinators)}"
            )
        return coordinators[entry_id]

    if len(coordinators) > 1:
        raise HomeAssistantError(
            "Ci sono più istanze configurate: specifica 'entry_id'. "
            f"Disponibili: {list(coordinators)}"
        )
    return next(iter(coordinators.values()))


def _risolvi_coordinator_e_pod_da_device(hass: HomeAssistant, device_id: str):
    """Da un device_id (scelto dal selettore 'device' nel form dell'azione,
    popolato dinamicamente con i dispositivi reali dell'integrazione)
    risale al coordinator e, se si tratta di un dispositivo per singolo
    POD E-Distribuzione, al POD specifico.

    Gli identifiers dei device sono sempre (DOMAIN, f"{entry_id}_{pod}")
    per i dispositivi per-POD (pcf_common, edistribuzione e areti, stesso
    formato in tutti e tre - vedi rispettivi sensor.py), o (DOMAIN, entry_id)
    per il dispositivo "account" di pcf_common (senza POD specifico: il
    recupero PCF vale sempre per l'intera configurazione insieme).
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"Dispositivo non trovato (device_id={device_id!r})")

    # Ricerca "a ritroso": si parte dalle nostre config entry attive e si
    # guarda quali dispositivi appartengono a ciascuna, invece di leggere
    # device.config_entries. Quel campo (insieme a
    # config_entries_subentries e primary_config_entry) e' deprecato dalla
    # ristrutturazione del device registry di HA 2026.8/2026.9, in favore
    # dei nuovi config_entry_id/config_subentry_id singoli: resta
    # funzionante fino a HA 2027.8, ma async_entries_for_config_entry e'
    # un helper ufficiale stabile che evita del tutto la questione,
    # senza dipendere ne' dal campo vecchio ne' da quello nuovo.
    # Il ciclo e' su hass.data[DOMAIN], cioe' sulle sole config entry di
    # questa integrazione (tipicamente una o due): nessun problema di costo.
    entry_id = next(
        (
            eid
            for eid in hass.data.get(DOMAIN, {})
            if any(d.id == device_id for d in dr.async_entries_for_config_entry(dev_reg, eid))
        ),
        None,
    )
    if entry_id is None:
        raise HomeAssistantError(
            "Il dispositivo selezionato non appartiene a nessuna configurazione "
            "attiva di contatore_letture."
        )

    coordinator = hass.data[DOMAIN][entry_id]

    pod = None
    if isinstance(coordinator, (EdistribuzioneCoordinator, AretiCoordinator)):
        prefisso = f"{entry_id}_"
        for dominio, identificativo in device.identifiers:
            if dominio == DOMAIN and identificativo.startswith(prefisso):
                candidato = identificativo[len(prefisso):]
                if candidato in coordinator.pods:
                    pod = candidato
                break

    return coordinator, pod


async def _async_registra_servizi(hass: HomeAssistant) -> None:
    """Registra le azioni dell'integrazione (una sola volta).

    'recupera_ticket' e' specifico del modello a ticket di Duereti/Unareti;
    'recupera_storico' e' condiviso da tutti i distributori che lo
    implementano (oggi: pcf e edistribuzione)."""
    if hass.services.has_service(DOMAIN, SERVICE_RECUPERA_STORICO):
        return

    async def _recupera_ticket(call: ServiceCall) -> None:
        coordinator = _trova_coordinator(hass, call.data.get("entry_id"), (PcfCoordinator,))
        data_da: date | None = call.data.get("data_da")
        data_a: date | None = call.data.get("data_a")
        await coordinator.async_forza_ticket(call.data["ticket"], data_da, data_a)

    async def _recupera_storico(call: ServiceCall) -> None:
        coordinator, pod = _risolvi_coordinator_e_pod_da_device(hass, call.data["device_id"])
        if not isinstance(coordinator, (PcfCoordinator, EdistribuzioneCoordinator, AretiCoordinator)):
            raise HomeAssistantError(
                "Il dispositivo selezionato non supporta il recupero storico."
            )
        if isinstance(coordinator, (EdistribuzioneCoordinator, AretiCoordinator)):
            await coordinator.async_recupera_storico(
                call.data["data_da"], call.data["data_a"], pod=pod
            )
        else:
            await coordinator.async_recupera_storico(call.data["data_da"], call.data["data_a"])

    hass.services.async_register(
        DOMAIN, SERVICE_RECUPERA_TICKET, _recupera_ticket, schema=SCHEMA_RECUPERA_TICKET
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECUPERA_STORICO, _recupera_storico, schema=SCHEMA_RECUPERA_STORICO
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Inizializza l'integrazione a partire da una config entry."""
    distributor_key = entry.data["distributor"]
    info = DISTRIBUTOR_REGISTRY[distributor_key]
    modulo = info["module"]

    if info["kind"] == "pcf":
        coordinator = modulo.create_coordinator(
            hass,
            entry,
            client_id=entry.data[CONF_CLIENT_ID],
            secret_id=entry.data[CONF_SECRET_ID],
            pods=entry.data[CONF_PODS],
        )
        # Il setup di una entry gia' configurata non fa chiamate di rete e non
        # puo' fallire qui: le credenziali sono gia' state validate quando la
        # si e' aggiunta. Gli errori di autenticazione/rete emergono dal
        # coordinator: PcfAuthError diventa ConfigEntryAuthFailed (Home
        # Assistant chiede di reinserire le credenziali tramite
        # async_step_reauth), tutto il resto e' un aggiornamento fallito che
        # HA riprova da solo.
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
        await _async_registra_servizi(hass)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            _LOGGER.warning(
                "Primo aggiornamento dati non riuscito per %s (l'autenticazione però funziona): "
                "l'integrazione resta attiva e riproverà automaticamente. Ultimo errore: %s",
                info["display_name"],
                coordinator.last_exception,
            )
        return True

    if info["kind"] == "edistribuzione":
        coordinator = modulo.create_coordinator(hass, entry)
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
        await _async_registra_servizi(hass)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        # async_config_entry_first_refresh (a differenza del ramo pcf sopra)
        # SOLLEVA ConfigEntryNotReady se il primo refresh fallisce: qui va
        # bene, dato che il refresh_token e' stato appena ottenuto nel config
        # flow e un fallimento immediato segnala verosimilmente un problema
        # reale (non il tipo di blocco WAF intermittente visto su Duereti).
        await coordinator.async_config_entry_first_refresh()
        return True

    if info["kind"] == "areti":
        coordinator = modulo.create_coordinator(hass, entry)
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
        await _async_registra_servizi(hass)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        # Come il ramo pcf sopra (non edistribuzione): un primo
        # aggiornamento fallito non deve impedire il setup (le
        # credenziali sono gia' state validate in config_flow). Nota: un
        # mese ancora senza dati NON e' un fallimento qui - il
        # coordinator lo gestisce restando semplicemente fermo sul suo
        # cursore, senza sollevare (vedi coordinator.py).
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            _LOGGER.warning(
                "Primo aggiornamento dati non riuscito per %s (l'autenticazione però funziona): "
                "l'integrazione resta attiva e riproverà automaticamente. Ultimo errore: %s",
                info["display_name"],
                coordinator.last_exception,
            )
        return True

    # Nessun altro "kind" atteso: se arriviamo qui e' un bug del registry.
    raise HomeAssistantError(f"Distributore con kind sconosciuto: {info['kind']}")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Scarica la config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    hass.data[DOMAIN].pop(entry.entry_id, None)

    rimanenti = list(hass.data[DOMAIN].values())
    pcf_rimasti = any(isinstance(c, PcfCoordinator) for c in rimanenti)
    edistribuzione_rimasti = any(isinstance(c, EdistribuzioneCoordinator) for c in rimanenti)
    areti_rimasti = any(isinstance(c, AretiCoordinator) for c in rimanenti)

    if not pcf_rimasti:
        hass.services.async_remove(DOMAIN, SERVICE_RECUPERA_TICKET)
    if not pcf_rimasti and not edistribuzione_rimasti and not areti_rimasti:
        hass.services.async_remove(DOMAIN, SERVICE_RECUPERA_STORICO)

    return True
