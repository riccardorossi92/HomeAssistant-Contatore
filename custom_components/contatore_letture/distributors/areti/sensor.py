"""Sensori diagnostici Areti: i dati veri finiscono in external statistics
(statistics.py). Questi servono solo a vedere a colpo d'occhio lo stato
dell'import, stessa filosofia minimale di pcf_common/edistribuzione (un
dispositivo "account" più uno per POD, poche entità diagnostiche - non un
sensore per ogni combinazione fascia/componente)."""
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
from .coordinator import AretiCoordinator


def _device_info_account(entry: ConfigEntry) -> DeviceInfo:
    """Dispositivo "genitore" per tutti i POD di questa config entry -
    stesso identico pattern di pcf_common/edistribuzione: la sua esistenza
    reale e' anche cio' che fa funzionare via_device dei dispositivi
    per-POD (vedi il commento equivalente in edistribuzione/sensor.py)."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Areti",
        manufacturer="Areti",
        model="Account portale",
    )


def _device_info_pod(entry: ConfigEntry, pod: str, id_padre: str | None = None) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{pod}")},
        name=f"POD {pod}",
        manufacturer="Areti",
        model="Punto di prelievo",
    )
    return collega_al_padre(info, {(DOMAIN, entry.entry_id)}, id_padre)


def build_areti_entities(hass, coordinator: AretiCoordinator) -> list[SensorEntity]:
    """Costruisce le entità sensor per una config entry Areti - chiamata
    dal sensor.py di contatore_letture (non è un platform entry point
    autonomo: un solo sensor.py serve tutti i distributori)."""
    entry = coordinator.entry

    id_padre = assicura_dispositivo_padre(hass, entry.entry_id, dict(_device_info_account(entry)))

    entities: list[SensorEntity] = [AretiPodConfiguratiSensor(coordinator, entry)]
    for pod in coordinator.pods:
        entities.append(AretiUltimaDataDisponibileSensor(coordinator, entry, pod, id_padre))
        entities.append(AretiConsumoMeseSensor(coordinator, entry, pod, id_padre))
    return entities


class AretiPodConfiguratiSensor(SensorEntity):
    """Vive sul dispositivo "account", equivalente di
    Pcf/EdistribuzionePodConfiguratiSensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: AretiCoordinator, entry: ConfigEntry) -> None:
        super().__init__()
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_pod_configurati"
        self._attr_name = "POD configurati"
        self._attr_native_value = len(coordinator.pods)
        self._attr_device_info = _device_info_account(entry)

    @property
    def extra_state_attributes(self):
        return {"pods": list(self.coordinator.pods)}


class AretiUltimaDataDisponibileSensor(
    CoordinatorEntity[AretiCoordinator], RestoreEntity, SensorEntity
):
    """Ultima data (locale) per cui sono realmente arrivati dati curva per
    un POD - legge lo stato reale delle external statistics, non solo se
    l'ultimo ciclo è girato con successo."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-check"

    def __init__(
        self, coordinator: AretiCoordinator, entry: ConfigEntry, pod: str,
        id_padre: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._attr_unique_id = f"{entry.entry_id}_{pod}_ultima_data_disponibile"
        self._attr_name = "Ultima data disponibile"
        self._attr_device_info = _device_info_pod(entry, pod, id_padre)
        self._ripristinato: date | None = None

    async def async_added_to_hass(self) -> None:
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


class AretiConsumoMeseSensor(CoordinatorEntity[AretiCoordinator], RestoreEntity, SensorEntity):
    """Consumo totale (kWh) dell'ultimo mese importato per un POD -
    equivalente di EdistribuzioneConsumoGiornoSensor, ma per mese (qui
    l'import è sempre mensile, non giornaliero). Volutamente senza
    state_class 'energy': quel valore vive sulle external statistics
    (statistics.py), non qui."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self, coordinator: AretiCoordinator, entry: ConfigEntry, pod: str,
        id_padre: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._attr_unique_id = f"{entry.entry_id}_{pod}_consumo_mese"
        self._attr_name = "Consumo ultimo mese importato"
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
        valore = dati_pod.get("kwh_ultimo_mese_importato")
        if valore is not None:
            return round(valore, 3)
        return self._ripristinato

    @property
    def extra_state_attributes(self):
        dati_pod = (self.coordinator.data or {}).get("by_pod", {}).get(self._pod, {})
        return {
            "mese": dati_pod.get("ultimo_mese_importato"),
            "prossimo_mese_da_importare": dati_pod.get("mese_da_importare"),
        }
