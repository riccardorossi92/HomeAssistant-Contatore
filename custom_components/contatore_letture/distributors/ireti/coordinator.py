"""DataUpdateCoordinator per Ireti.

Design completo (e il "perché") in documentation/protocols/ireti-protocol.md,
sezione "Design del coordinator". In sintesi:

  - measures-loadprofiles accetta un range di date arbitrario e restituisce
    un loadProfiles[] con un elemento per ogni giorno disponibile in quel
    range (confermato: un mese intero in una sola chiamata, issue #6) -
    stesso meccanismo di edistribuzione: ogni ciclo si chiede in UNA SOLA
    richiesta l'intervallo dal più vecchio giorno ancora in coda fino al
    giorno atteso (oggi - RITARDO_DATI_GIORNI). I giorni ricevuti escono
    dalla coda, quelli mancanti ci entrano e vengono riprovati ai cicli
    successivi, abbandonati (con un avviso nei log) dopo
    ABBANDONO_CODA_DOPO_GIORNI senza dati. A differenza di una finestra
    fissa "abbastanza larga" (l'approccio della prima versione di questo
    file), un giorno che non arriva mai non sparisce silenziosamente: lo
    dice nei log, ed è comunque recuperabile a mano con
    contatore_letture.recupera_storico.

  - Il refresh_token Keycloak dura solo 30 minuti: inutilizzabile con un
    ciclo giornaliero, quindi non viene nemmeno conservato - si rifà
    login da zero (password grant) a ogni ciclo, come Areti (che non ha
    nemmeno un refresh_token). Credenziali non valide sollevano
    ConfigEntryAuthFailed (come pcf_common - HA gestisce da solo il
    reauth), non un semplice UpdateFailed.

  - 'podType' (necessario nel payload di measures-loadprofiles) si legge
    da /users/exabeat/history, endpoint MAI confermato con dati reali
    (vedi api.py): se fallisce o non lo contiene, si ricade su "orario"
    (l'unico valore osservato finora) con un avviso nei log invece di far
    fallire l'intero ciclo - un'ipotesi sbagliata qui produce nella
    peggiore delle ipotesi un payload che il portale rifiuta con un
    errore esplicito (IretiApiError -> UpdateFailed), non un dato
    silenziosamente sbagliato.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ...const import DOMAIN
from .api import IretiApiClient, IretiApiError
from .auth import IretiAuthClient, IretiAuthError, IretiInvalidCredentials
from .const import (
    ABBANDONO_CODA_DOPO_GIORNI,
    CONF_GIORNI_DA_RIPROVARE,
    CONF_PASSWORD,
    CONF_PODS,
    CONF_USERNAME,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    MAX_GIORNI_IN_CODA,
    MAX_GIORNI_RECUPERO_STORICO,
    RITARDO_DATI_GIORNI,
)
from .statistics import async_get_ultima_data_disponibile, async_import_curva_giorni

_LOGGER = logging.getLogger(__name__)

# Unico podType osservato finora (issue #6) - usato come ripiego quando
# /users/exabeat/history fallisce o non lo contiene (vedi docstring).
_POD_TYPE_DI_RIPIEGO = "orario"

# Un'unica chiamata a measures-loadprofiles ha coperto un mese intero
# (issue #6): per il recupero storico su periodi più lunghi si procede a
# blocchi di questa dimensione invece di chiedere anni interi in una sola
# richiesta mai provata.
_GIORNI_PER_BLOCCO_RECUPERO = 31


def _finestra_iso(giorno_da: date, giorno_a: date) -> tuple[str, str]:
    """[giorno_da, giorno_a] (estremi inclusi, ora locale) -> (startDate,
    endDate) UTC ISO 8601 nel formato osservato nella cattura reale
    (issue #6): 'YYYY-MM-DDTHH:MM:SS.000Z'."""
    inizio_locale = datetime.combine(giorno_da, time.min).replace(
        tzinfo=dt_util.DEFAULT_TIME_ZONE
    )
    fine_locale = datetime.combine(giorno_a, time(23, 59, 59)).replace(
        tzinfo=dt_util.DEFAULT_TIME_ZONE
    )
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    return dt_util.as_utc(inizio_locale).strftime(fmt), dt_util.as_utc(fine_locale).strftime(fmt)


def _blocchi_nel_periodo(data_da: date, data_a: date) -> list[tuple[date, date]]:
    """[data_da, data_a] spezzato in blocchi di al più
    _GIORNI_PER_BLOCCO_RECUPERO giorni ciascuno, in ordine."""
    blocchi: list[tuple[date, date]] = []
    inizio = data_da
    while inizio <= data_a:
        fine = min(inizio + timedelta(days=_GIORNI_PER_BLOCCO_RECUPERO - 1), data_a)
        blocchi.append((inizio, fine))
        inizio = fine + timedelta(days=1)
    return blocchi


def _giorni_nel_periodo(data_da: date, data_a: date) -> list[date]:
    """Elenco dei giorni compresi nell'intervallo, estremi inclusi."""
    giorni, cursore = [], data_da
    while cursore <= data_a:
        giorni.append(cursore)
        cursore += timedelta(days=1)
    return giorni


def _giorno_di(elemento: dict[str, Any]) -> date | None:
    """loadProfileDate ('DD/MM/YYYY HH:MM:SS +ZZZZ') -> solo la data, o
    None se il campo manca/non è nel formato atteso."""
    try:
        return datetime.strptime(str(elemento["loadProfileDate"])[:10], "%d/%m/%Y").date()
    except (KeyError, TypeError, ValueError):
        return None


def _giorni_ricevuti(load_profiles: list[dict[str, Any]]) -> set[date]:
    """Giorni effettivamente presenti nella risposta con dati validi
    (energyType 'A1' + almeno un campione - stesso filtro di
    statistics.py), scartando elementi vuoti o di altro tipo."""
    giorni: set[date] = set()
    for elemento in load_profiles:
        if elemento.get("energyType") != "A1" or not elemento.get("sampleValues"):
            continue
        giorno = _giorno_di(elemento)
        if giorno is not None:
            giorni.add(giorno)
    return giorni


def _kwh_del_giorno(load_profiles: list[dict[str, Any]], giorno: date) -> float | None:
    """Somma dei sampleValues del giorno indicato dentro una risposta
    multi-giorno, o None se quel giorno non c'è."""
    for elemento in load_profiles:
        if elemento.get("energyType") == "A1" and _giorno_di(elemento) == giorno:
            return sum(
                float(v) for v in (elemento.get("sampleValues") or []) if v not in (None, "")
            )
    return None


class IretiCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} (Ireti)",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
            config_entry=entry,
        )
        self.entry = entry
        self.pods: list[str] = list(entry.data[CONF_PODS])
        self._session = async_create_clientsession(hass)
        self._auth = IretiAuthClient(self._session)
        # Risolti la prima volta che servono e tenuti in memoria per la
        # vita del coordinator (identificativi stabili, persi a un
        # riavvio di Home Assistant ma ririsolti senza problemi al primo
        # ciclo successivo) - stesso principio della cache codiceBP/
        # codiceFiscale di Areti.
        self._customer_tax_code_vat: str | None = None
        self._pod_type: dict[str, str] = {}
        self._pod_type_fallback_avvisato: set[str] = set()

    async def _async_login(self) -> IretiApiClient:
        try:
            access_token = await self._auth.async_login(
                self.entry.data[CONF_USERNAME], self.entry.data[CONF_PASSWORD]
            )
        except IretiInvalidCredentials as err:
            raise ConfigEntryAuthFailed(f"Credenziali Ireti non valide: {err}") from err
        except IretiAuthError as err:
            raise UpdateFailed(f"Login Ireti fallito: {err}") from err
        return IretiApiClient(self._session, access_token)

    async def _async_customer_tax_code_vat(self, api: IretiApiClient) -> str:
        if self._customer_tax_code_vat is None:
            consumer = await api.async_get_consumer(self.entry.data[CONF_USERNAME])
            tax_code = consumer.get("pIVA") or consumer.get("codFiscale")
            if not tax_code:
                raise IretiApiError(
                    f"Né pIVA né codFiscale presenti nell'anagrafica consumer: {consumer!r}"
                )
            self._customer_tax_code_vat = tax_code
        return self._customer_tax_code_vat

    async def _async_pod_type(
        self, api: IretiApiClient, pod: str, tax_code: str, start_iso: str, end_iso: str
    ) -> str:
        """podType per un POD, con ripiego su _POD_TYPE_DI_RIPIEGO se
        /users/exabeat/history fallisce o non lo contiene (endpoint mai
        confermato - vedi docstring del modulo e api.py)."""
        if pod in self._pod_type:
            return self._pod_type[pod]

        pod_type = _POD_TYPE_DI_RIPIEGO
        try:
            storia = await api.async_get_history(pod, tax_code, start_iso, end_iso)
            pod_type = storia.get("podType") or _POD_TYPE_DI_RIPIEGO
        except IretiApiError as err:
            if pod not in self._pod_type_fallback_avvisato:
                _LOGGER.warning(
                    "POD %s: /users/exabeat/history non ha risposto correttamente "
                    "(%s) - uso '%s' come podType di ripiego (unico valore "
                    "confermato finora). Vedi documentation/protocols/ireti-protocol.md.",
                    pod, err, _POD_TYPE_DI_RIPIEGO,
                )
                self._pod_type_fallback_avvisato.add(pod)

        self._pod_type[pod] = pod_type
        return pod_type

    # ------------------------------------------------------------------
    # Coda dei giorni da riprovare, PER POD (stesso meccanismo di
    # edistribuzione/coordinator.py - vedi quel file per i commenti
    # estesi sul formato/retrocompatibilità, qui non duplicati).
    # ------------------------------------------------------------------

    def _leggi_code(self) -> dict[str, dict[str, date]]:
        grezzo = self.entry.data.get(CONF_GIORNI_DA_RIPROVARE) or {}
        oggi = dt_util.now().date()

        def _con_date(coda: dict) -> dict[str, date]:
            risultato: dict[str, date] = {}
            for giorno, valore in coda.items():
                try:
                    risultato[giorno] = date.fromisoformat(valore)
                except (TypeError, ValueError):
                    risultato[giorno] = oggi
            return risultato

        return {pod: _con_date(coda) for pod, coda in grezzo.items()}

    def _scrivi_code(self, code: dict[str, dict[str, date]]) -> None:
        oggi = dt_util.now().date()
        pulite: dict[str, dict[str, str]] = {}
        for pod, coda in code.items():
            pulita = {
                giorno: da
                for giorno, da in coda.items()
                if (oggi - da).days < ABBANDONO_CODA_DOPO_GIORNI
            }
            abbandonati = set(coda) - set(pulita)
            if abbandonati:
                _LOGGER.warning(
                    "POD %s: giorni abbandonati dopo %d giorni in coda senza dati da "
                    "Ireti: %s. Se servono, richiedili con l'azione "
                    "contatore_letture.recupera_storico.",
                    pod,
                    ABBANDONO_CODA_DOPO_GIORNI,
                    ", ".join(sorted(abbandonati)),
                )

            if len(pulita) > MAX_GIORNI_IN_CODA:
                tenuti = sorted(pulita, reverse=True)[:MAX_GIORNI_IN_CODA]
                scartati = set(pulita) - set(tenuti)
                _LOGGER.warning(
                    "POD %s: coda dei giorni da riprovare oltre %d elementi: scarto i "
                    "più vecchi (%s)",
                    pod,
                    MAX_GIORNI_IN_CODA,
                    ", ".join(sorted(scartati)),
                )
                pulita = {g: pulita[g] for g in tenuti}

            if pulita:
                pulite[pod] = {g: da.isoformat() for g, da in pulita.items()}

        if pulite != self.entry.data.get(CONF_GIORNI_DA_RIPROVARE):
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_GIORNI_DA_RIPROVARE: pulite},
            )

    def _accoda_giorno(self, pod: str, giorno: date) -> None:
        code = self._leggi_code()
        coda = code.setdefault(pod, {})
        chiave = giorno.isoformat()
        if chiave in coda:
            _LOGGER.info(
                "POD %s: giorno %s ancora senza dati da Ireti, in coda da %d giorni "
                "(max %d)",
                pod,
                chiave,
                (dt_util.now().date() - coda[chiave]).days,
                ABBANDONO_CODA_DOPO_GIORNI,
            )
        else:
            coda[chiave] = dt_util.now().date()
            _LOGGER.info(
                "POD %s: giorno %s senza dati da Ireti, messo in coda per riprovare "
                "(max %d giorni)",
                pod,
                chiave,
                ABBANDONO_CODA_DOPO_GIORNI,
            )
        self._scrivi_code(code)

    def _rimuovi_dalla_coda(self, pod: str, giorni: list[date]) -> None:
        code = self._leggi_code()
        coda = code.get(pod, {})
        rimossi = [g.isoformat() for g in giorni if g.isoformat() in coda]
        if not rimossi:
            return
        for chiave in rimossi:
            del coda[chiave]
        _LOGGER.info("POD %s: dati ricevuti per %s, rimossi dalla coda", pod, ", ".join(rimossi))
        self._scrivi_code(code)

    async def _prossima_richiesta(self, pod: str) -> tuple[date, date] | None:
        """Decide che intervallo chiedere per questo POD in questo ciclo.

        Stessa logica di edistribuzione: chiede in UNA SOLA richiesta
        l'intervallo che va dal più vecchio giorno arretrato fino al
        giorno atteso (oggi - RITARDO_DATI_GIORNI) - a meno che
        quest'ultimo non risulti già coperto dalle statistiche esistenti.
        Nessun orario di cortesia da aspettare (a differenza di
        edistribuzione): non abbiamo ancora nessuna osservazione su
        quando Ireti pubblica i dati, quindi si prova a ogni ciclo.
        """
        oggi = dt_util.now().date()
        atteso = oggi - timedelta(days=RITARDO_DATI_GIORNI)

        code = self._leggi_code()
        coda = code.get(pod, {})
        arretrati = sorted(date.fromisoformat(g) for g in coda if date.fromisoformat(g) < atteso)
        if arretrati:
            # Una sola richiesta dal più vecchio arretrato al giorno atteso,
            # con lo stesso margine di edistribuzione (150 giorni): in
            # pratica non si attiva quasi mai, la coda tiene al massimo
            # MAX_GIORNI_IN_CODA giorni.
            return max(arretrati[0], atteso - timedelta(days=150)), atteso

        ultima_disponibile = await async_get_ultima_data_disponibile(self.hass, pod)
        if ultima_disponibile and ultima_disponibile >= atteso:
            return None

        return atteso, atteso

    # ------------------------------------------------------------------
    # Ciclo di polling automatico
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        api = await self._async_login()
        tax_code = await self._async_customer_tax_code_vat(api)

        by_pod: dict[str, dict] = {}
        for pod in self.pods:
            richiesta = await self._prossima_richiesta(pod)
            kwh_ultimo_giorno_importato = None
            ultimo_giorno_importato = None

            if richiesta is not None:
                data_da, data_a = richiesta
                start_iso, end_iso = _finestra_iso(data_da, data_a)
                pod_type = await self._async_pod_type(api, pod, tax_code, start_iso, end_iso)
                try:
                    load_profiles = await api.async_get_measures_loadprofiles(
                        pod, tax_code, pod_type, start_iso, end_iso
                    )
                except IretiApiError as err:
                    # I giorni richiesti vanno in coda invece di andare
                    # persi: al ciclo successivo 'atteso' sarebbe già avanzato.
                    for giorno in _giorni_nel_periodo(data_da, data_a):
                        self._accoda_giorno(pod, giorno)
                    raise UpdateFailed(
                        f"Errore recuperando la curva di carico Ireti per il POD {pod}: {err}"
                    ) from err

                await async_import_curva_giorni(self.hass, pod, load_profiles)

                # L'intervallo può tornare parziale (i giorni più recenti
                # non ancora pubblicati): quelli ricevuti escono dalla
                # coda, quelli mancanti ci entrano.
                ricevuti = _giorni_ricevuti(load_profiles)
                richiesti = _giorni_nel_periodo(data_da, data_a)
                self._rimuovi_dalla_coda(pod, [g for g in richiesti if g in ricevuti])
                for giorno in richiesti:
                    if giorno not in ricevuti:
                        self._accoda_giorno(pod, giorno)

                if ricevuti:
                    ultimo = max(ricevuti)
                    kwh_ultimo_giorno_importato = _kwh_del_giorno(load_profiles, ultimo)
                    ultimo_giorno_importato = ultimo.isoformat()

            ultima_data_disponibile = await async_get_ultima_data_disponibile(self.hass, pod)
            by_pod[pod] = {
                "ultimo_giorno_importato": ultimo_giorno_importato,
                "kwh_ultimo_giorno_importato": kwh_ultimo_giorno_importato,
                "ultima_data_disponibile": (
                    ultima_data_disponibile.isoformat() if ultima_data_disponibile else None
                ),
            }

        return {"by_pod": by_pod}

    # ------------------------------------------------------------------
    # Recupero storico manuale (azione contatore_letture.recupera_storico)
    # ------------------------------------------------------------------

    async def async_recupera_storico(
        self, data_da: date, data_a: date, pod: str | None = None
    ) -> None:
        """Recupera e importa la curva di carico per [data_da, data_a].

        Se 'pod' è omesso, lo fa per TUTTI i POD configurati sulla entry;
        se specificato, solo per quello. Il periodo viene spezzato in
        blocchi di _GIORNI_PER_BLOCCO_RECUPERO giorni (vedi il commento
        sulla costante): non è noto se measures-loadprofiles accetti
        range più ampi di quello confermato (un mese) in una sola
        richiesta, quindi non si rischia. I giorni recuperati con successo
        vengono anche tolti dalla coda del ciclo automatico, se ci erano
        finiti.

        Solleva HomeAssistantError se al termine non è stato importato
        nessun giorno: l'azione è manuale e lanciata dall'interfaccia,
        dove un fallimento silenzioso è indistinguibile da un successo
        (stesso principio già applicato a PCF/E-Distribuzione/Areti,
        issue #4).
        """
        if pod is not None and pod not in self.pods:
            raise ServiceValidationError(
                f"Il POD '{pod}' non è configurato su questa istanza. "
                f"POD configurati: {', '.join(self.pods)}"
            )
        pod_da_recuperare = [pod] if pod else list(self.pods)

        if data_da > data_a:
            raise ServiceValidationError(
                f"La data di inizio ({data_da}) è successiva a quella di fine ({data_a})."
            )

        giorni_totali = (data_a - data_da).days + 1
        if giorni_totali > MAX_GIORNI_RECUPERO_STORICO:
            raise ServiceValidationError(
                f"Intervallo di {giorni_totali} giorni troppo ampio per una singola "
                f"richiesta (limite di cortesia: {MAX_GIORNI_RECUPERO_STORICO} giorni). "
                "Ripeti l'azione su periodi più corti."
            )

        api = await self._async_login()
        tax_code = await self._async_customer_tax_code_vat(api)
        blocchi = _blocchi_nel_periodo(data_da, data_a)

        _LOGGER.info(
            "Recupero storico Ireti avviato: %s - %s (%d blocchi, POD: %s)",
            data_da, data_a, len(blocchi), ", ".join(pod_da_recuperare),
        )

        fallimenti: list[str] = []
        giorni_importati = 0

        for pod_corrente in pod_da_recuperare:
            start_iso, end_iso = _finestra_iso(data_da, data_a)
            pod_type = await self._async_pod_type(api, pod_corrente, tax_code, start_iso, end_iso)

            trovati = 0
            for blocco_da, blocco_a in blocchi:
                blocco_start_iso, blocco_end_iso = _finestra_iso(blocco_da, blocco_a)
                try:
                    load_profiles = await api.async_get_measures_loadprofiles(
                        pod_corrente, tax_code, pod_type, blocco_start_iso, blocco_end_iso
                    )
                except IretiApiError as err:
                    _LOGGER.warning(
                        "POD %s: errore recuperando %s - %s: %s",
                        pod_corrente, blocco_da, blocco_a, err,
                    )
                    fallimenti.append(f"{pod_corrente}/{blocco_da}-{blocco_a}: {err}")
                    continue

                await async_import_curva_giorni(self.hass, pod_corrente, load_profiles)
                ricevuti_blocco = _giorni_ricevuti(load_profiles)
                self._rimuovi_dalla_coda(pod_corrente, sorted(ricevuti_blocco))
                trovati += len(ricevuti_blocco)
                giorni_importati += len(ricevuti_blocco)

            _LOGGER.info(
                "POD %s: recupero storico completato, %d giorni trovati nel periodo %s - %s",
                pod_corrente, trovati, data_da, data_a,
            )
            if trovati == 0:
                fallimenti.append(f"{pod_corrente}: nessun giorno disponibile nel periodo richiesto")

        if giorni_importati == 0:
            raise HomeAssistantError(
                f"Nessun dato importato per il periodo {data_da} - {data_a}. "
                + "; ".join(fallimenti)
            )
