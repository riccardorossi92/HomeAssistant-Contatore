"""Sensor platform for e-Distribuzione.

Un dispositivo per POD (la config entry può averne più di uno), con per
ciascuno:
  - one sensor per (magnitude, fascia) from the latest published `reading`
    e.g. sensor.edistribuzione_<pod>_ea_t1, ..._er_t3
  - one sensor per (magnitude, fascia) power peak from monthly time-of-use
    e.g. sensor.edistribuzione_<pod>_pot_t1

The coordinator already fetched `reading` and `time_of_use` per POD
(coordinator.data["by_pod"][pod]); this platform just reshapes the latest
entries into entities.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ...const import DOMAIN
from .coordinator import EdistribuzioneCoordinator

_ENERGY_MAGNITUDES = {"EA": "Energia attiva", "ER": "Energia reattiva"}
_FASCE = ("T1", "T2", "T3", "T4", "T5", "T6")


def build_edistribuzione_entities(
    coordinator: EdistribuzioneCoordinator,
) -> list[SensorEntity]:
    """Costruisce le entità sensor per una config entry E-Distribuzione,
    un dispositivo per ciascun POD configurato.

    Chiamata dal sensor.py di contatore_letture (non è un platform entry
    point autonomo: un solo sensor.py serve tutti i distributori)."""
    entities: list[SensorEntity] = []
    for pod in coordinator.pods:
        for magnitude in _ENERGY_MAGNITUDES:
            for fascia in _FASCE:
                entities.append(
                    EdistribuzioneReadingSensor(coordinator, pod, magnitude, fascia)
                )
        for fascia in _FASCE:
            entities.append(EdistribuzionePowerPeakSensor(coordinator, pod, fascia))

    return entities


class _BaseEdistribuzioneSensor(CoordinatorEntity[EdistribuzioneCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EdistribuzioneCoordinator, pod: str) -> None:
        super().__init__(coordinator)
        self._pod = pod

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._pod)},
            "name": f"e-Distribuzione {self._pod}",
            "manufacturer": "e-Distribuzione",
            "model": "POD",
        }

    def _dati_pod(self) -> dict:
        return self.coordinator.data.get("by_pod", {}).get(self._pod, {})


class EdistribuzioneReadingSensor(_BaseEdistribuzioneSensor):
    """Latest published cumulative reading for a given (magnitude, fascia)."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "kWh"

    def __init__(
        self, coordinator: EdistribuzioneCoordinator, pod: str, magnitude: str, fascia: str
    ) -> None:
        super().__init__(coordinator, pod)
        self._magnitude = magnitude
        self._fascia = fascia
        self._attr_unique_id = f"{pod}_{magnitude.lower()}_{fascia.lower()}"
        self._attr_name = f"{_ENERGY_MAGNITUDES[magnitude]} {fascia}"

    @property
    def native_value(self):
        readings = self._dati_pod().get("reading", [])
        if not readings:
            return None
        latest = readings[-1]
        for slot in latest.get("publishedSlots", []):
            if slot.get("magnitude") == self._magnitude and slot.get("slotId") == self._fascia:
                return slot.get("value")
        return None


class EdistribuzionePowerPeakSensor(_BaseEdistribuzioneSensor):
    """Latest published power peak (POT) for a given fascia."""

    _attr_native_unit_of_measurement = "kW"

    def __init__(self, coordinator: EdistribuzioneCoordinator, pod: str, fascia: str) -> None:
        super().__init__(coordinator, pod)
        self._fascia = fascia
        self._attr_unique_id = f"{pod}_pot_{fascia.lower()}"
        self._attr_name = f"Picco di potenza {fascia}"

    @property
    def native_value(self):
        readings = self._dati_pod().get("reading", [])
        if not readings:
            return None
        latest = readings[-1]
        for slot in latest.get("publishedPowerPeaks", []):
            if slot.get("magnitude") == "POT" and slot.get("slotId") == self._fascia:
                return slot.get("value")
        return None
