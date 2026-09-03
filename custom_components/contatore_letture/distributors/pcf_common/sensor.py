"""Sensori diagnostici: i dati veri finiscono in external statistics (statistics.py).
Questi sensori servono solo a vedere a colpo d'occhio lo stato dell'import."""
from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from ...const import DOMAIN
from ...device_helpers import assicura_dispositivo_padre, collega_al_padre
from .const import CONF_PODS


def _device_info_account(entry: ConfigEntry, display_name: str) -> DeviceInfo:
    """Dispositivo 'account': raggruppa i sensori generali dell'istanza."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=display_name,
        model="Account API",
    )


def _device_info_pod(
    entry: ConfigEntry, pod: str, display_name: str, id_padre: str | None = None
) -> DeviceInfo:
    """Dispositivo per un singolo POD, agganciato all'account.

    Il collegamento usa via_device_id su HA 2026.8+ e via_device sulle
    versioni precedenti: vedi device_helpers.collega_al_padre.
    """
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{pod}")},
        name=f"POD {pod}",
        manufacturer=display_name,
        model="Punto di prelievo",
    )
    return collega_al_padre(info, {(DOMAIN, entry.entry_id)}, id_padre)


def build_pcf_entities(hass, coordinator, entry: ConfigEntry, display_name: str) -> list:
    """Costruisce le entità sensor condivise PCF per una config entry.

    Chiamata dal sensor.py di contatore_letture (non è un platform entry
    point autonomo: un solo sensor.py serve tutti i distributori)."""
    pods = entry.data.get(CONF_PODS, [])

    # Il dispositivo "account" va registrato PRIMA di quelli per POD, che lo
    # referenziano come padre: su HA 2026.8+ serve il suo ID interno, che
    # esiste solo dopo la registrazione.
    id_padre = assicura_dispositivo_padre(
        hass, entry.entry_id, dict(_device_info_account(entry, display_name))
    )

    entità = [
        PcfUltimoImportSensor(coordinator, entry, display_name),
        PcfStatoAttesaSensor(coordinator, entry, display_name),
        PcfPodConfiguratiSensor(coordinator, entry, pods, display_name),
    ]
    for pod_conf in pods:
        entità.append(
            PcfUltimaDataDisponibileSensor(
                coordinator, entry, pod_conf["pod"], display_name, id_padre
            )
        )
        entità.append(
            PcfConsumoPeriodoSensor(coordinator, entry, pod_conf["pod"], display_name, id_padre)
        )

    return entità


class PcfUltimoImportSensor(CoordinatorEntity, RestoreEntity, SensorEntity):
    """Mostra la data dell'ultimo import di curve riuscito."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, coordinator, entry: ConfigEntry, display_name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ultimo_import"
        self._attr_name = f"{display_name} - Ultimo import"
        self._attr_device_info = _device_info_account(entry, display_name)
        self._ripristinato: str | None = None

    @property
    def available(self) -> bool:
        """Disponibile finché l'autenticazione funziona.

        È un'entità del dispositivo "Account API", che rappresenta lo stato
        della connessione al distributore, non i dati di un POD: dipende quindi
        dall'esito di requestToken, non da quello del recupero dati. Se a
        fallire è requestExport o requestResult (WAF, file non pronto, ecc.)
        questa entità resta disponibile, ed è proprio dove si leggono gli
        attributi `stato` e `ultimo_errore` per capire cosa è andato storto.
        """
        return getattr(self.coordinator, "token_ok", True)

    async def async_added_to_hass(self) -> None:
        """Recupera l'ultimo valore noto dopo un riavvio.

        I dati del coordinator vivono in memoria: dopo un riavvio restano
        vuoti finché non arriva un nuovo import (che può essere lontano ore o
        giorni), e senza ripristino il sensore mostrerebbe "Sconosciuto"
        pur avendo dati validi in dashboard.
        """
        await super().async_added_to_hass()
        ultimo_stato = await self.async_get_last_state()
        if ultimo_stato and ultimo_stato.state not in (None, "unknown", "unavailable"):
            self._ripristinato = ultimo_stato.state

    @property
    def native_value(self):
        if self.coordinator.data:
            valore = self.coordinator.data.get("ultimo_aggiornamento")
            if valore is not None:
                return valore
        return self._ripristinato

    @property
    def extra_state_attributes(self):
        if self.coordinator.data:
            return {
                "pod_aggiornati": self.coordinator.data.get("pod_aggiornati"),
                "stato": self.coordinator.data.get("stato"),
                "ultimo_errore": self.coordinator.data.get("ultimo_errore"),
            }
        return {}


class PcfUltimaDataDisponibileSensor(CoordinatorEntity, RestoreEntity, SensorEntity):
    """Mostra l'ultima data per cui sono realmente arrivati dati curva per un POD.

    A differenza di '<Distributore> - Ultimo import' (che riflette solo quando è
    girato l'ultimo ciclo con successo), questo legge lo stato reale delle
    external statistics per il POD: se l'ultima richiesta ha restituito dati
    solo fino a metà periodo, questo sensore lo riflette correttamente.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-check"

    # Nessun override di `available`: vale il comportamento di
    # CoordinatorEntity, cioè indisponibile quando l'ultimo aggiornamento è
    # fallito. È voluto: questa è un'entità del dispositivo POD, e un
    # fallimento nel recupero dati riguarda proprio i dati di quel POD. Chi
    # guarda il POD deve accorgersi che qualcosa non va; il dettaglio
    # dell'errore resta leggibile sul dispositivo "Account API".

    def __init__(
        self, coordinator, entry: ConfigEntry, pod: str, display_name: str,
        id_padre: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._attr_unique_id = f"{entry.entry_id}_{pod}_ultima_data_disponibile"
        self._attr_name = "Ultima data disponibile"
        self._attr_device_info = _device_info_pod(entry, pod, display_name, id_padre)
        self._ripristinato: date | None = None

    async def async_added_to_hass(self) -> None:
        """Recupera l'ultimo valore noto dopo un riavvio.

        I dati del coordinator vivono in memoria: dopo un riavvio restano
        vuoti finché non gira un ciclo che li ripopola, e senza ripristino il
        sensore mostrerebbe "Sconosciuto" pur avendo statistiche valide nel
        database.
        """
        await super().async_added_to_hass()
        ultimo_stato = await self.async_get_last_state()
        if ultimo_stato and ultimo_stato.state not in (None, "unknown", "unavailable"):
            try:
                self._ripristinato = date.fromisoformat(ultimo_stato.state)
            except ValueError:
                self._ripristinato = None

    @property
    def native_value(self) -> date | None:
        if self.coordinator.data:
            valore = self.coordinator.data.get("ultime_date_per_pod", {}).get(self._pod)
            if valore is not None:
                return date.fromisoformat(valore)
        return self._ripristinato


class PcfConsumoPeriodoSensor(CoordinatorEntity, RestoreEntity, SensorEntity):
    """Mostra il consumo totale (kWh) dell'ultimo periodo importato per un POD.

    Non è una statistica progressiva come le external statistics: è solo un
    numero di riepilogo dell'ultimo import riuscito, comodo per un colpo
    d'occhio senza aprire il grafico della Energy Dashboard. Volutamente
    senza device_class/state_class 'energy': quel valore va sulle external
    statistics (statistics.py), non su questo sensore. Un device_class
    'energy' con un valore che si azzera/ricomincia ad ogni periodo (non
    cumulativo da sempre) non è comunque compatibile con nessuno degli
    state_class ammessi da HA per quel device_class ('total'/'total_increasing').
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self, coordinator, entry: ConfigEntry, pod: str, display_name: str,
        id_padre: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._pod = pod
        self._attr_unique_id = f"{entry.entry_id}_{pod}_consumo_periodo"
        self._attr_name = "Consumo ultimo periodo"
        self._attr_device_info = _device_info_pod(entry, pod, display_name, id_padre)
        self._ripristinato: float | None = None

    async def async_added_to_hass(self) -> None:
        """Recupera l'ultimo valore noto dopo un riavvio (vedi
        PcfUltimoImportSensor per il motivo)."""
        await super().async_added_to_hass()
        ultimo_stato = await self.async_get_last_state()
        if ultimo_stato and ultimo_stato.state not in (None, "unknown", "unavailable"):
            try:
                self._ripristinato = float(ultimo_stato.state)
            except ValueError:
                self._ripristinato = None

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            valore = self.coordinator.data.get("totale_kwh_periodo_per_pod", {}).get(self._pod)
            if valore is not None:
                return valore
        return self._ripristinato

    @property
    def extra_state_attributes(self):
        if self.coordinator.data:
            return {"periodo": self.coordinator.data.get("periodo_importato")}
        return {}


class PcfStatoAttesaSensor(SensorEntity):
    """Minuti di attesa del file corrente (requestResult in polling).

    Utile per automazioni/notifiche: se resta alto per troppo tempo (ore),
    può segnalare un problema (WAF, job bloccato lato Duereti, ecc.).
    Si aggiorna da solo ogni 30s (should_poll) perché durante l'attesa il
    coordinator non emette aggiornamenti push.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-sand"
    _attr_native_unit_of_measurement = "min"
    _attr_should_poll = True

    def __init__(self, coordinator, entry: ConfigEntry, display_name: str) -> None:
        super().__init__()
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_stato_attesa"
        self._attr_name = f"{display_name} - Attesa file (minuti)"
        self._attr_native_value = 0
        self._attr_device_info = _device_info_account(entry, display_name)

    async def async_update(self) -> None:
        pending_since = getattr(self.coordinator, "pending_since", None)
        if pending_since is None:
            self._attr_native_value = 0
        else:
            delta = dt_util.utcnow() - pending_since
            self._attr_native_value = round(delta.total_seconds() / 60)

    @property
    def extra_state_attributes(self):
        return {
            "in_attesa": getattr(self.coordinator, "pending_since", None) is not None,
            "ticket": getattr(self.coordinator, "pending_ticket", None),
            "in_attesa_dal": (
                self.coordinator.pending_since.isoformat()
                if getattr(self.coordinator, "pending_since", None)
                else None
            ),
        }


class PcfPodConfiguratiSensor(SensorEntity):
    """Mostra quanti POD sono configurati in questa istanza dell'integrazione."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator, entry: ConfigEntry, pods: list[dict], display_name: str) -> None:
        super().__init__()
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_pod_configurati"
        self._attr_name = f"{display_name} - POD configurati"
        self._attr_native_value = len(pods)
        self._pods = [p["pod"] for p in pods]
        self._attr_device_info = _device_info_account(entry, display_name)

    @property
    def extra_state_attributes(self):
        return {"pods": self._pods}
