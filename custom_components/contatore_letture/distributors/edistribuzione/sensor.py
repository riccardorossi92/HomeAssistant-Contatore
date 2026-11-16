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
from homeassistant.const import UnitOfEnergy
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from ...const import DOMAIN
from ...consumo_periodo import crea_sensori_consumo, crea_sensori_immissione
from ...device_helpers import assicura_dispositivo_padre, collega_al_padre
from .const import MAGNITUDE_IMMISSIONE, MAGNITUDE_PRELIEVO
from .coordinator import EdistribuzioneCoordinator
from .fasce import FASCE, mese_precedente


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
        entities.extend(
            crea_sensori_consumo(
                hass, entry, coordinator, pod, _device_info_pod(entry, pod, id_padre)
            )
        )
        # Mese precedente per fascia (F1-F3) del prelievo. Quelli dell'immissione,
        # come i sensori di periodo dell'immissione, nascono dopo, solo sui POD
        # con impianto (vedi async_segui_impianti).
        for fascia in FASCE:
            entities.append(
                EdistribuzioneFasciaSensor(
                    coordinator, entry, pod, MAGNITUDE_PRELIEVO, fascia, id_padre
                )
            )
    return entities


def _entita_immissione(
    hass, coordinator: EdistribuzioneCoordinator, pod: str, id_padre: str | None
) -> list[SensorEntity]:
    """Sensori dell'energia immessa (A2) di un POD con impianto: periodo
    (mese, mese precedente, trimestre, anno, trimestre precedente) e fasce."""
    entry = coordinator.entry
    entities: list[SensorEntity] = [
        EdistribuzioneFasciaSensor(coordinator, entry, pod, MAGNITUDE_IMMISSIONE, fascia, id_padre)
        for fascia in FASCE
    ]
    entities.extend(
        crea_sensori_immissione(
            hass, entry, coordinator, pod, _device_info_pod(entry, pod, id_padre)
        )
    )
    return entities


@callback
def async_segui_impianti(hass, coordinator: EdistribuzioneCoordinator, async_add_entities) -> None:
    """Aggiunge i sensori di immissione di un POD appena risulta avere un impianto.

    `HasPlant` arriva da getSupplies, che al caricamento della piattaforma non
    e' ancora stato letto (e puo' fallire): per questo le entita' si creano
    dopo, al primo giro del coordinator in cui il POD risulta con impianto.
    Una volta create restano (un impianto che sparisce e' un caso da gestire a
    mano), e `unique_id` stabili le ritrovano a ogni riavvio.
    """
    entry = coordinator.entry
    aggiunti: set[str] = set()

    @callback
    def _controlla() -> None:
        by_pod = (coordinator.data or {}).get("by_pod", {})
        da_aggiungere = [
            pod
            for pod in coordinator.pods
            if pod not in aggiunti and by_pod.get(pod, {}).get("con_impianto")
        ]
        if not da_aggiungere:
            return
        id_padre = assicura_dispositivo_padre(
            hass, entry.entry_id, dict(_device_info_account(entry))
        )
        entities: list[SensorEntity] = []
        for pod in da_aggiungere:
            aggiunti.add(pod)
            entities.extend(_entita_immissione(hass, coordinator, pod, id_padre))
        async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(_controlla))
    _controlla()


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
        # Ultimo valore valido (kWh) e giorno a cui si riferisce: restano
        # nei cicli senza dati nuovi e si ripristinano dopo un riavvio.
        self._ultimo_valore: float | None = None
        self._ultimo_giorno: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        ultimo_stato = await self.async_get_last_state()
        if ultimo_stato and ultimo_stato.state not in (None, "unknown", "unavailable"):
            try:
                self._ultimo_valore = float(ultimo_stato.state)
                self._ultimo_giorno = ultimo_stato.attributes.get("giorno")
            except ValueError:
                self._ultimo_valore = None

        # Il coordinator può aver già fatto un giro prima che il sensore
        # fosse aggiunto: se ha un valore, vale più di quello ripristinato.
        self._memorizza_ultimo()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._memorizza_ultimo()
        super()._handle_coordinator_update()

    def _memorizza_ultimo(self) -> None:
        """Se il coordinator ha un valore nuovo lo memorizza, insieme al
        giorno a cui si riferisce; altrimenti resta l'ultimo noto."""
        dati_pod = (self.coordinator.data or {}).get("by_pod", {}).get(self._pod, {})
        valore = dati_pod.get("kwh_ultimo_giorno_importato")
        if valore is not None:
            self._ultimo_valore = round(valore, 3)
            self._ultimo_giorno = dati_pod.get("ultimo_giorno_importato")

    @property
    def native_value(self) -> float | None:
        return self._ultimo_valore

    @property
    def extra_state_attributes(self):
        return {"giorno": self._ultimo_giorno}


class EdistribuzioneFasciaSensor(CoordinatorEntity[EdistribuzioneCoordinator], SensorEntity):
    """kWh di una fascia (F1, F2 o F3) nel mese precedente, prelevati o immessi.

    Dato ufficiale del contatore (querymonthlytimeofuse), che E-Distribuzione
    pubblica il 1 del mese: finche' non c'e' il valore e' `unknown`, e a ogni
    cambio mese si azzera invece di mostrare il mese di prima. Come i sensori
    di periodo, senza state_class: non duplica le statistiche importate.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: EdistribuzioneCoordinator,
        entry: ConfigEntry,
        pod: str,
        grandezza: str,
        fascia: str,
        id_padre: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._grandezza = grandezza
        self._fascia = fascia
        tipo = "immissione" if grandezza == MAGNITUDE_IMMISSIONE else "consumo"
        self._attr_unique_id = f"{entry.entry_id}_{pod}_{tipo}_{fascia.lower()}_mese_precedente"
        self._attr_name = f"{tipo.capitalize()} {fascia} mese precedente"
        self._attr_device_info = _device_info_pod(entry, pod, id_padre)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # A mezzanotte del cambio mese "mese precedente" e' un altro mese: il
        # valore va riletto subito, senza aspettare il giro del coordinator.
        self.async_on_remove(
            async_track_time_change(self.hass, self._a_mezzanotte, hour=0, minute=0, second=30)
        )

    @callback
    def _a_mezzanotte(self, _now) -> None:
        self.async_write_ha_state()

    def _fasce_del_mese(self) -> tuple[str, dict]:
        """(AAAAMM atteso, fasce di quel mese per questa grandezza)."""
        _inizio, _fine, anno_mese = mese_precedente(dt_util.now().date())
        dati_pod = (self.coordinator.data or {}).get("by_pod", {}).get(self._pod, {})
        fasce = dati_pod.get("fasce") or {}
        if fasce.get("mese") != anno_mese:
            return anno_mese, {}
        return anno_mese, fasce.get(self._grandezza) or {}

    @property
    def native_value(self) -> float | None:
        valore = self._fasce_del_mese()[1].get(self._fascia)
        return None if valore is None else round(valore, 3)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        anno_mese, fasce = self._fasce_del_mese()
        if not fasce:
            return {}
        return {"mese": f"{anno_mese[:4]}-{anno_mese[4:]}"}
