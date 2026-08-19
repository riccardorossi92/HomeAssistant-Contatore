"""Sensor platform for e-Distribuzione.

Exposes, per configured POD:
  - one sensor per (magnitude, fascia) from the latest published `reading`
    e.g. sensor.edistribuzione_<pod>_ea_t1, ..._er_t3
  - one sensor per (magnitude, fascia) power peak from monthly time-of-use
    e.g. sensor.edistribuzione_<pod>_pot_t1

The coordinator already fetched `reading` and `time_of_use`; this platform
just reshapes the latest entries into entities. Extend as needed once you've
decided which of these you actually want long-term-statistics style handling
for (similar to what you did for Octopus IT).
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
    """Costruisce le entità sensor per una config entry E-Distribuzione.

    Chiamata dal sensor.py di contatore_letture (non è un platform entry
    point autonomo: un solo sensor.py serve tutti i distributori)."""
    entities: list[SensorEntity] = []
    for magnitude in _ENERGY_MAGNITUDES:
        for fascia in _FASCE:
            entities.append(
                EdistribuzioneReadingSensor(coordinator, magnitude, fascia)
            )
    for fascia in _FASCE:
        entities.append(EdistribuzionePowerPeakSensor(coordinator, fascia))

    return entities


class _BaseEdistribuzioneSensor(CoordinatorEntity[EdistribuzioneCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EdistribuzioneCoordinator) -> None:
        super().__init__(coordinator)
        self._pod = coordinator.pod

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._pod)},
            "name": f"e-Distribuzione {self._pod}",
            "manufacturer": "e-Distribuzione",
            "model": "POD",
        }


class EdistribuzioneReadingSensor(_BaseEdistribuzioneSensor):
    """Latest published cumulative reading for a given (magnitude, fascia)."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "kWh"

    def __init__(
        self, coordinator: EdistribuzioneCoordinator, magnitude: str, fascia: str
    ) -> None:
        super().__init__(coordinator)
        self._magnitude = magnitude
        self._fascia = fascia
        self._attr_unique_id = f"{self._pod}_{magnitude.lower()}_{fascia.lower()}"
        self._attr_name = f"{_ENERGY_MAGNITUDES[magnitude]} {fascia}"

    @property
    def native_value(self):
        readings = self.coordinator.data.get("reading", [])
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

    def __init__(self, coordinator: EdistribuzioneCoordinator, fascia: str) -> None:
        super().__init__(coordinator)
        self._fascia = fascia
        self._attr_unique_id = f"{self._pod}_pot_{fascia.lower()}"
        self._attr_name = f"Picco di potenza {fascia}"

    @property
    def native_value(self):
        readings = self.coordinator.data.get("reading", [])
        if not readings:
            return None
        latest = readings[-1]
        for slot in latest.get("publishedPowerPeaks", []):
            if slot.get("magnitude") == "POT" and slot.get("slotId") == self._fascia:
                return slot.get("value")
        return None
