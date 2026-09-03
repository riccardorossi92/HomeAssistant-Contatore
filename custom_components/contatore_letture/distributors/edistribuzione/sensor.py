"""Sensori diagnostici E-Distribuzione: i dati veri finiscono in external
statistics (statistics.py). Questi sensori servono solo a vedere a colpo
d'occhio lo stato dell'import, stessa filosofia minimale di pcf_common
(vedi distributors/pcf_common/sensor.py) - non un sensore per ogni
combinazione fascia/grandezza come nella prima versione: la maggior
parte delle fasce (T4-T6) è comunque null sui contratti reali osservati
finora (monorario/bioraro), quindi era per lo più rumore.
"""
from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ...const import DOMAIN
from ...device_helpers import assicura_dispositivo_padre, collega_al_padre
from .coordinator import EdistribuzioneCoordinator


def _device_info_account(entry: ConfigEntry) -> DeviceInfo:
    """Dispositivo "genitore" per tutti i POD di questa config entry.

    Prima mancava: _device_info_pod dichiarava via_device puntando a
    questo device, ma nessun sensore lo creava mai davvero (nessuna
    entità lo usava come proprio device_info) - Home Assistant segnalava
    un warning per un via_device riferito a un dispositivo inesistente.
    Trovato nei log di produzione il 21/08/2026 (v0.2.3). Fix: aggiunto
    almeno un sensore (EdistribuzionePodConfiguratiSensor) che usa
    davvero questo device_info, cosi' viene creato per davvero - stesso
    identico pattern di pcf_common.sensor._device_info_account /
    PcfPodConfiguratiSensor.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="E-Distribuzione",
        manufacturer="E-Distribuzione",
        model="Account API",
    )


def _device_info_pod(entry: ConfigEntry, pod: str, id_padre: str | None = None) -> DeviceInfo:
    """Dispositivo per un singolo POD, agganciato all'account.

    Il collegamento usa via_device_id su HA 2026.8+ e via_device sulle
    versioni precedenti: vedi device_helpers.collega_al_padre.
    """
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{pod}")},
        name=f"POD {pod}",
        manufacturer="E-Distribuzione",
        model="Punto di prelievo",
    )
    return collega_al_padre(info, {(DOMAIN, entry.entry_id)}, id_padre)


def build_edistribuzione_entities(
    hass, coordinator: EdistribuzioneCoordinator
) -> list[SensorEntity]:
    """Costruisce le entità sensor per una config entry E-Distribuzione,
    un dispositivo per ciascun POD configurato più uno "account" comune.

    Chiamata dal sensor.py di contatore_letture (non è un platform entry
    point autonomo: un solo sensor.py serve tutti i distributori)."""
    entry = coordinator.entry

    # Il dispositivo "account" va registrato PRIMA di quelli per POD, che lo
    # referenziano come padre: su HA 2026.8+ serve il suo ID interno, che
    # esiste solo dopo la registrazione.
    id_padre = assicura_dispositivo_padre(
        hass, entry.entry_id, dict(_device_info_account(entry))
    )

    entities: list[SensorEntity] = [
        EdistribuzionePodConfiguratiSensor(coordinator, entry)
    ]
    for pod in coordinator.pods:
        entities.append(
            EdistribuzioneUltimaDataDisponibileSensor(coordinator, entry, pod, id_padre)
        )
        entities.append(
            EdistribuzioneConsumoGiornoSensor(coordinator, entry, pod, id_padre)
        )
    return entities


class EdistribuzionePodConfiguratiSensor(SensorEntity):
    """Mostra quanti POD sono configurati in questa istanza - vive sul
    dispositivo "account" (equivalente di PcfPodConfiguratiSensor), la
    cui esistenza reale è anche ciò che fa funzionare via_device dei
    dispositivi per-POD."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: EdistribuzioneCoordinator, entry: ConfigEntry) -> None:
        super().__init__()
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_pod_configurati"
        self._attr_name = "POD configurati"
        self._attr_native_value = len(coordinator.pods)
        self._attr_device_info = _device_info_account(entry)

    @property
    def extra_state_attributes(self):
        return {"pods": list(self.coordinator.pods)}


class EdistribuzioneUltimaDataDisponibileSensor(
    CoordinatorEntity[EdistribuzioneCoordinator], RestoreEntity, SensorEntity
):
    """Mostra l'ultima data per cui sono realmente arrivati dati curva per
    un POD - legge lo stato reale delle external statistics, non solo se
    l'ultimo ciclo è girato con successo (equivalente esatto di
    PcfUltimaDataDisponibileSensor)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-check"

    def __init__(
        self, coordinator: EdistribuzioneCoordinator, entry: ConfigEntry, pod: str,
        id_padre: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._attr_unique_id = f"{entry.entry_id}_{pod}_ultima_data_disponibile"
        self._attr_name = "Ultima data disponibile"
        self._attr_device_info = _device_info_pod(entry, pod, id_padre)
        self._ripristinato: date | None = None

    async def async_added_to_hass(self) -> None:
        """Recupera l'ultimo valore noto dopo un riavvio: i dati del
        coordinator vivono in memoria e restano vuoti finché non gira un
        ciclo che li ripopola."""
        await super().async_added_to_hass()
        ultimo_stato = await self.async_get_last_state()
        if ultimo_stato and ultimo_stato.state not in (None, "unknown", "unavailable"):
            try:
                self._ripristinato = date.fromisoformat(ultimo_stato.state)
            except ValueError:
                self._ripristinato = None

    @property
    def native_value(self) -> date | None:
        dati_pod = (self.coordinator.data or {}).get("by_pod", {}).get(self._pod, {})
        valore = dati_pod.get("ultima_data_disponibile")
        if valore is not None:
            return date.fromisoformat(valore)
        return self._ripristinato


class EdistribuzioneConsumoGiornoSensor(
    CoordinatorEntity[EdistribuzioneCoordinator], RestoreEntity, SensorEntity
):
    """Mostra il consumo totale (kWh) dell'ultimo giorno importato per un
    POD - equivalente di PcfConsumoPeriodoSensor, ma per un giorno invece
    che per un periodo arbitrario (qui l'import è sempre giornaliero).
    Volutamente senza state_class 'energy': quel valore vive sulle
    external statistics (statistics.py), non qui."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self, coordinator: EdistribuzioneCoordinator, entry: ConfigEntry, pod: str,
        id_padre: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._attr_unique_id = f"{entry.entry_id}_{pod}_consumo_giorno"
        self._attr_name = "Consumo ultimo giorno importato"
        self._attr_device_info = _device_info_pod(entry, pod, id_padre)
        self._ripristinato: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        ultimo_stato = await self.async_get_last_state()
        if ultimo_stato and ultimo_stato.state not in (None, "unknown", "unavailable"):
            try:
                self._ripristinato = float(ultimo_stato.state)
            except ValueError:
                self._ripristinato = None

    @property
    def native_value(self) -> float | None:
        dati_pod = (self.coordinator.data or {}).get("by_pod", {}).get(self._pod, {})
        valore = dati_pod.get("kwh_ultimo_giorno_importato")
        if valore is not None:
            return round(valore, 3)
        return self._ripristinato

    @property
    def extra_state_attributes(self):
        dati_pod = (self.coordinator.data or {}).get("by_pod", {}).get(self._pod, {})
        return {"giorno": dati_pod.get("ultimo_giorno_curva_richiesto")}
