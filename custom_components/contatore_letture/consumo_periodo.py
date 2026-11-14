"""Sensori "consumo del mese corrente / mese precedente / trimestre / anno" per POD.

I consumi veri vivono nelle external statistics (`contatore_letture:<pod>_energia`),
che non sono entità: non si possono mettere in una card (es. card-bolletta) né
usare in un template o in un'automazione. Questi sensori le rendono leggibili
come normali entità, sommando i giorni del periodo in corso (issue #20).

Un solo modulo per tutti i distributori: tutti scrivono lo stesso statistic_id
(statistics_common.sanitize_statistic_id), quindi la lettura è identica. Ogni
distributore li aggiunge alle proprie entità con `crea_sensori_consumo`.

Per ogni POD c'è un solo `ConsumiPod` che rilegge le statistiche una volta e
alimenta i sensori. Si aggiorna quando il coordinator finisce un giro (è lì
che arrivano dati nuovi) e a mezzanotte, perché al cambio di mese/trimestre/anno
il periodo corrente riparte da zero anche se non arriva nessun dato.

Il valore è `unknown` finché il periodo non ha nemmeno un giorno importato: non
è uno zero (sarebbe un consumo inventato). L'attributo `dati_fino_al` dice fino
a quale giorno il totale è completo.

I distributori che pubblicano a mese chiuso (Duereti, Unareti, RetiPiù, Reti
Valtellina Valchiavenna, Areti) non hanno mai dati nel mese in corso: per loro
il "mese corrente" resterebbe sempre `unknown`, quindi non lo creano
(`mese_corrente=False`) e restano mese precedente, trimestre e anno. Il mese
precedente è il dato utile per la bolletta: si riempie quando il distributore
pubblica quel mese.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .statistics_common import sanitize_statistic_id

_LOGGER = logging.getLogger(__name__)

# periodo -> (parte dello unique_id, nome dell'entità). Gli unique_id dei
# periodi correnti sono quelli della 0.14.0 e non vanno cambiati. L'ordine è
# quello di creazione delle entità. Il trimestre è quello solare (gen-mar,
# apr-giu, lug-set, ott-dic), non quello della fornitura.
PERIODI: dict[str, tuple[str, str]] = {
    "mese": ("mese_corrente", "Consumo mese corrente"),
    "mese_precedente": ("mese_precedente", "Consumo mese precedente"),
    "trimestre": ("trimestre_corrente", "Consumo trimestre corrente"),
    "anno": ("anno_corrente", "Consumo anno corrente"),
}


def intervallo_periodo(periodo: str, oggi: date) -> tuple[date, date]:
    """Primo e ultimo giorno del periodo, rispetto a `oggi`.

    Per i periodi correnti l'ultimo giorno è `oggi`; per il mese precedente è
    l'ultimo giorno del mese di calendario prima di quello che contiene `oggi`.
    """
    inizio_mese = oggi.replace(day=1)
    if periodo == "mese":
        return inizio_mese, oggi
    if periodo == "mese_precedente":
        fine = inizio_mese - timedelta(days=1)
        return fine.replace(day=1), fine
    if periodo == "trimestre":
        return date(oggi.year, 3 * ((oggi.month - 1) // 3) + 1, 1), oggi
    if periodo == "anno":
        return date(oggi.year, 1, 1), oggi
    raise ValueError(f"Periodo sconosciuto: {periodo!r}")


def somma_periodo(
    kwh_per_giorno: dict[date, float], inizio: date, fine: date
) -> tuple[float | None, date | None]:
    """Somma i kWh dei giorni in [inizio, fine].

    Ritorna (totale, ultimo giorno con dati), o (None, None) se nel periodo
    non c'è nessun giorno importato.
    """
    giorni = [g for g in kwh_per_giorno if inizio <= g <= fine]
    if not giorni:
        return None, None
    return sum(kwh_per_giorno[g] for g in giorni), max(giorni)


async def leggi_consumi_giornalieri(
    hass: HomeAssistant, statistic_id: str, dal: date
) -> dict[date, float]:
    """kWh per giorno locale, dal giorno `dal` in poi, dalle external statistics.

    Legge le ore (`state` = kWh della singola ora, come in
    statistics_common.leggi_serie_esistente) e le somma per data locale: così un
    giorno di 23 o 25 ore (cambio d'ora) somma quelle effettivamente presenti, e
    un giorno senza dati semplicemente manca. Non si usa l'aggregazione
    giornaliera di Home Assistant (period="day" con il solo `change`): provata
    su un recorder vero, non ritorna righe.

    Prima aspetta che il recorder abbia svuotato la coda: l'import accoda la
    scrittura senza attenderla, e questa lettura parte subito dopo.
    """
    recorder = get_instance(hass)
    await recorder.async_block_till_done()
    risultato = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.start_of_local_day(dal),
        None,
        {statistic_id},
        "hour",
        None,
        {"state"},
    )

    kwh: dict[date, float] = {}
    for riga in risultato.get(statistic_id, []):
        stato = riga.get("state")
        if stato is None:
            continue
        giorno = dt_util.as_local(dt_util.utc_from_timestamp(riga["start"])).date()
        kwh[giorno] = kwh.get(giorno, 0.0) + float(stato)
    return kwh


class ConsumiPod:
    """Rilegge i consumi di un POD e li distribuisce ai tre sensori."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, pod: str) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._pod = pod
        self.statistic_id = sanitize_statistic_id(pod)
        # periodo -> (totale kWh, primo giorno, ultimo giorno, ultimo giorno con dati)
        self.valori: dict[str, tuple[float | None, date, date, date | None]] = {}
        self._sensori: list[SensorEntity] = []
        self._annulla: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()

    def registra(self, sensore: SensorEntity) -> None:
        """Chiamata da ogni sensore quando viene aggiunto a Home Assistant.

        Il primo avvia i trigger di aggiornamento e la prima lettura; sono i
        sensori stessi a farlo (non la costruzione) perché solo allora esiste
        uno stato su cui scrivere.
        """
        self._sensori.append(sensore)
        if len(self._sensori) == 1:
            self._annulla.append(self._coordinator.async_add_listener(self._al_giro_coordinator))
            self._annulla.append(
                async_track_time_change(self._hass, self._a_mezzanotte, hour=0, minute=0, second=30)
            )
            self._pianifica_aggiornamento()

    def rimuovi(self, sensore: SensorEntity) -> None:
        if sensore in self._sensori:
            self._sensori.remove(sensore)
        if not self._sensori:
            for annulla in self._annulla:
                annulla()
            self._annulla.clear()

    @callback
    def _al_giro_coordinator(self) -> None:
        self._pianifica_aggiornamento()

    @callback
    def _a_mezzanotte(self, _now: datetime) -> None:
        self._pianifica_aggiornamento()

    def _pianifica_aggiornamento(self) -> None:
        self._entry.async_create_background_task(
            self._hass, self.async_aggiorna(), f"consumo_periodo_{self._pod}"
        )

    async def async_aggiorna(self) -> None:
        # Il lock serializza i giri ravvicinati: ciascuno rilegge lo stato
        # attuale, quindi l'ultimo vince e nessuno vede dati a metà.
        async with self._lock:
            oggi = dt_util.now().date()
            intervalli = {p: intervallo_periodo(p, oggi) for p in PERIODI}
            try:
                # Il mese precedente, a gennaio, cade nell'anno prima.
                kwh = await leggi_consumi_giornalieri(
                    self._hass, self.statistic_id, min(i for i, _ in intervalli.values())
                )
            except Exception:  # noqa: BLE001 - un errore del recorder non deve rompere i sensori
                _LOGGER.warning(
                    "POD %s: impossibile leggere le statistiche di consumo, "
                    "i sensori di periodo restano al valore precedente",
                    self._pod,
                    exc_info=True,
                )
                return

            for periodo, (inizio, fine) in intervalli.items():
                totale, ultimo = somma_periodo(kwh, inizio, fine)
                self.valori[periodo] = (totale, inizio, fine, ultimo)

            for sensore in list(self._sensori):
                sensore.async_write_ha_state()


class ConsumoPeriodoSensor(SensorEntity):
    """kWh consumati da un POD nel mese corrente, nel mese precedente, nel
    trimestre o nell'anno in corso.

    Volutamente senza state_class: il valore si azzera a ogni cambio di periodo
    e Home Assistant, con una state_class, ne compilerebbe statistiche a lungo
    termine che duplicano quelle già importate (e lo proporrebbe come fonte
    nella Energy Dashboard, contandolo due volte). Il device_class `energy`
    senza state_class è valido e basta a far convertire l'unità alle card.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self, consumi: ConsumiPod, entry: ConfigEntry, pod: str, periodo: str, device_info
    ) -> None:
        self._consumi = consumi
        self._periodo = periodo
        suffisso, nome = PERIODI[periodo]
        self._attr_unique_id = f"{entry.entry_id}_{pod}_consumo_{suffisso}"
        self._attr_name = nome
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._consumi.registra(self)

    async def async_will_remove_from_hass(self) -> None:
        self._consumi.rimuovi(self)
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> float | None:
        totale = self._consumi.valori.get(self._periodo, (None,))[0]
        return None if totale is None else round(totale, 3)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        valore = self._consumi.valori.get(self._periodo)
        if valore is None:
            return {}
        _totale, inizio, fine, ultimo = valore
        return {
            "dal": inizio.isoformat(),
            "al": fine.isoformat(),
            "dati_fino_al": ultimo.isoformat() if ultimo else None,
        }


def crea_sensori_consumo(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator,
    pod: str,
    device_info,
    *,
    mese_corrente: bool = True,
) -> list[SensorEntity]:
    """I sensori di periodo di un POD: mese corrente (se `mese_corrente`), mese
    precedente, trimestre e anno.

    `mese_corrente=False` per i distributori che pubblicano a mese chiuso: il
    mese in corso non ha mai dati, il sensore sarebbe sempre `unknown`.
    """
    consumi = ConsumiPod(hass, entry, coordinator, pod)
    return [
        ConsumoPeriodoSensor(consumi, entry, pod, p, device_info)
        for p in PERIODI
        if mese_corrente or p != "mese"
    ]
