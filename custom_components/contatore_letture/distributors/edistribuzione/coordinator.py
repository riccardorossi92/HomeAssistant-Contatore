"""DataUpdateCoordinator for e-Distribuzione.

Supporta PIÙ POD sulla stessa config entry (stessa credenziale, stesso
account autenticato): a differenza di pcf_common, qui non serve un dato
fiscale per ciascuno (sono tutti già associati all'utenza loggata).

Ogni ciclo:
- refresh del token;
- per ciascun POD configurato, se è il momento (coda + orario), la curva
  di carico del giorno prima, importata nella Energy Dashboard.

'reading'/'time_of_use' (letture ufficiali mensili) NON sono recuperati
automaticamente qui - lo erano fino a un certo punto dello sviluppo, ma
nessun sensore li legge dalla riduzione a 3 sensori diagnostici (v0.2.3):
erano scaricati ogni ora per niente, solo carico inutile verso le API del
distributore (rimosso in v0.3.0). Restano disponibili solo tramite l'API
stessa (EdistribuzioneApiClient.async_get_reading/
async_get_monthly_time_of_use), da richiamare esplicitamente se in futuro
serve esporli.

async_recupera_storico accetta un parametro 'pod' opzionale: se omesso,
recupera lo storico per TUTTI i POD della entry.
"""
from __future__ import annotations

import logging
from datetime import date, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ...const import DOMAIN
from .api import EdistribuzioneApiClient, EdistribuzioneApiError
from .auth import (
    EdistribuzioneAuthClient,
    EdistribuzioneAuthError,
    EdistribuzioneTokenNonValido,
)
from .const import (
    ABBANDONO_CODA_DOPO_GIORNI,
    CONF_DATA_INSTALLAZIONE,
    CONF_GIORNI_DA_RIPROVARE,
    CONF_ORA_RICHIESTA,
    CONF_ORARIO_RICHIESTA,
    CONF_PODS,
    CONF_REFRESH_TOKEN,
    CONF_SUPPLIES,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    MAX_GIORNI_IN_CODA,
    MAX_MESI_PER_RICHIESTA,
    MAX_MESI_STORICO,
    RITARDO_DATI_GIORNI,
    orario_casuale,
    orario_da_opzioni,
)
from .statistics import async_get_ultima_data_disponibile, async_import_curva_giornaliera

_LOGGER = logging.getLogger(__name__)


def _stesso_giorno_mesi_prima(giorno: date, mesi: int) -> date:
    """Lo stesso giorno del mese, `mesi` mesi prima (ripiegando sull'ultimo
    giorno del mese se quello di partenza non esiste, es. 31 -> 30)."""
    indice = giorno.year * 12 + giorno.month - 1 - mesi
    anno, mese = divmod(indice, 12)
    mese += 1
    successivo = date(anno + (mese == 12), mese % 12 + 1, 1)
    ultimo_del_mese = (successivo - timedelta(days=1)).day
    return date(anno, mese, min(giorno.day, ultimo_del_mese))


def _giorni_nel_periodo(data_da: date, data_a: date) -> list[date]:
    """Elenco dei giorni compresi nell'intervallo, estremi inclusi."""
    giorni, cursore = [], data_da
    while cursore <= data_a:
        giorni.append(cursore)
        cursore += timedelta(days=1)
    return giorni


def _giorni_ricevuti(curva: list[dict]) -> set[date]:
    """Giorni effettivamente presenti nella risposta (campo sampleDate,
    formato YYYYMMDD), scartando quelli senza campioni."""
    giorni: set[date] = set()
    for elemento in curva:
        readings = elemento.get("readings", {})
        if not readings.get("sampleValues"):
            continue
        grezzo = readings.get("sampleDate")
        try:
            giorni.add(date(int(grezzo[:4]), int(grezzo[4:6]), int(grezzo[6:8])))
        except (TypeError, ValueError, IndexError):
            _LOGGER.warning("sampleDate non interpretabile, giorno ignorato: %r", grezzo)
    return giorni


def _kwh_del_giorno(curva: list[dict], giorno: date) -> float | None:
    """Totale kWh di un singolo giorno dentro una risposta multi-giorno."""
    atteso = giorno.strftime("%Y%m%d")
    for elemento in curva:
        readings = elemento.get("readings", {})
        if readings.get("sampleDate") == atteso:
            return sum(float(c.get("val", 0)) for c in readings.get("sampleValues", []))
    return None


def _curva_ha_dati(curva: list[dict]) -> bool:
    """True se la risposta di async_get_daily_load_profile contiene
    davvero dei campioni, non solo una struttura vuota."""
    return bool(curva) and bool(curva[0].get("readings", {}).get("sampleValues"))


class EdistribuzioneCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} (E-Distribuzione)",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
            config_entry=entry,
        )
        self.entry = entry
        self.pods: list[str] = list(entry.data[CONF_PODS])
        session = async_get_clientsession(hass)
        self._auth = EdistribuzioneAuthClient(session)
        self._api = EdistribuzioneApiClient(session, access_token="")
        self._orario_ripiego: time | None = None
        self._supplies: dict[str, dict] | None = None
        self._assicura_orario_richiesta()

    async def _async_ensure_token(self) -> None:
        refresh_token = self.entry.data[CONF_REFRESH_TOKEN]
        try:
            tokens = await self._auth.async_refresh_access_token(refresh_token)
        except EdistribuzioneTokenNonValido as err:
            # refresh_token scaduto o revocato (cambio password, sessioni
            # chiuse dal portale...): solo un nuovo accesso lo sostituisce.
            # Home Assistant avvia il reauth, che propone l'accesso dal
            # browser mantenendo POD, opzioni e storico.
            raise ConfigEntryAuthFailed(
                f"Accesso E-Distribuzione scaduto, serve un nuovo accesso: {err}"
            ) from err
        except EdistribuzioneAuthError as err:
            # Antibot, app_blocked, rete: dipende da E-Distribuzione e il
            # token resta valido, quindi si riprova al ciclo successivo
            # invece di chiedere un nuovo accesso che non cambierebbe nulla.
            raise UpdateFailed(f"Token refresh failed: {err}") from err

        self._api.update_token(tokens.access_token)

        if tokens.refresh_token != refresh_token:
            new_data = dict(self.entry.data)
            new_data[CONF_REFRESH_TOKEN] = tokens.refresh_token
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    # ------------------------------------------------------------------
    # Orario configurabile + coda dei giorni da riprovare, PER POD
    # (stesso meccanismo di pcf_common/coordinator.py, duplicato invece di
    # condiviso: vedi la nota in const.py su RITARDO_DATI_GIORNI)
    # ------------------------------------------------------------------

    def _assicura_orario_richiesta(self) -> None:
        """Entry senza orario (create prima dell'orario casuale, o in un
        modo che non lo imposta): ne sceglie uno casuale e lo salva nelle
        opzioni, cosi' resta lo stesso ai riavvii. Un orario impostato a
        mano (anche nel vecchio formato CONF_ORA_RICHIESTA) non si tocca."""
        opzioni = self.entry.options
        if CONF_ORARIO_RICHIESTA in opzioni or CONF_ORA_RICHIESTA in opzioni:
            return
        orario = orario_casuale()
        self.hass.config_entries.async_update_entry(
            self.entry, options={**opzioni, CONF_ORARIO_RICHIESTA: orario}
        )
        _LOGGER.info("Orario della richiesta giornaliera scelto a caso: %s", orario)

    @property
    def _orario_richiesta(self) -> time:
        """Orario (locale) a partire dal quale chiedere la curva del giorno
        prima. Configurabile dalle opzioni: non sappiamo ancora quando
        E-Distribuzione pubblica i dati (vedi nota in const.py), quindi se
        capitano richieste spesso vuote conviene spostarlo più avanti."""
        orario = orario_da_opzioni(self.entry.options)
        if orario is not None:
            return orario
        # Valore rovinato: uno casuale per questo avvio, senza salvarlo,
        # per non sovrascrivere quello che c'e' nelle opzioni.
        if self._orario_ripiego is None:
            self._orario_ripiego = dt_util.parse_time(orario_casuale())
            _LOGGER.warning(
                "Orario richiesta non valido nelle opzioni (%r): uso le %s",
                self.entry.options.get(
                    CONF_ORARIO_RICHIESTA, self.entry.options.get(CONF_ORA_RICHIESTA)
                ),
                self._orario_ripiego.strftime("%H:%M"),
            )
        return self._orario_ripiego

    def _leggi_code(self) -> dict[str, dict[str, date]]:
        """Code dei giorni da riprovare, UNA PER POD:
        {pod: {giorno ISO: data di primo inserimento}}.

        La data di primo inserimento fa da timer: un giorno viene abbandonato
        dopo ABBANDONO_CODA_DOPO_GIORNI a prescindere dal numero di tentativi
        (vedi _scrivi_code).

        Retrocompatibile con i formati precedenti: coda piatta a singolo POD
        ({data: X}), e valore X come numero di tentativi invece che come data.
        In entrambi i casi non si sa da quando il giorno è in coda, quindi gli
        si assegna "oggi", una tantum al primo aggiornamento dopo l'upgrade.
        """
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

        # Formato "vecchio" a singolo POD (una sola coda piatta, da quando la
        # entry supportava un solo POD): la spostiamo sotto l'unico POD.
        if grezzo and not any(isinstance(v, dict) for v in grezzo.values()):
            if len(self.pods) == 1:
                return {self.pods[0]: _con_date(grezzo)}
            return {}
        return {pod: _con_date(coda) for pod, coda in grezzo.items()}

    def _scrivi_code(self, code: dict[str, dict[str, date]]) -> None:
        """Salva le code, scartando i giorni troppo vecchi e limitandone il numero, per ciascun POD."""
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
                    "E-Distribuzione: %s. Se servono, richiedili con l'azione "
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
                "POD %s: giorno %s ancora senza dati da E-Distribuzione, in coda da "
                "%d giorni (max %d)",
                pod,
                chiave,
                (dt_util.now().date() - coda[chiave]).days,
                ABBANDONO_CODA_DOPO_GIORNI,
            )
        else:
            coda[chiave] = dt_util.now().date()
            _LOGGER.info(
                "POD %s: giorno %s senza dati da E-Distribuzione, messo in coda per "
                "riprovare (max %d giorni)",
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

        Stessa logica di pcf_common, applicata per singolo POD: al primo
        avvio chiede subito il giorno atteso (per verificare da subito che
        POD/token siano validi); nei cicli successivi aspetta l'orario
        configurato, poi chiede in UNA SOLA richiesta l'intervallo che va
        dal più vecchio giorno arretrato fino al giorno atteso - a meno che
        quest'ultimo non risulti già coperto dalle statistiche esistenti.

        Chiedere un intervallo invece di un giorno alla volta (verificato
        il 20/08/2026 che l'endpoint restituisce fino a 181 giorni in
        un'unica risposta) evita anche un buco silenzioso: prima, quando
        c'erano arretrati, il giorno atteso non veniva richiesto affatto e
        il giorno dopo 'atteso' era già avanzato - quel giorno non lo
        chiedeva più nessuno.

        CONF_DATA_INSTALLAZIONE è condivisa da tutti i POD della entry
        (riflette quando la config entry è stata creata, non quando un
        singolo POD è stato aggiunto): un POD aggiunto in seguito tramite
        le opzioni parte comunque subito al primo ciclo utile, dato che la
        coda per quel POD è vuota e async_get_ultima_data_disponibile
        restituisce None finché non ha dati.
        """
        oggi = dt_util.now().date()
        installazione = self.entry.data.get(CONF_DATA_INSTALLAZIONE)
        atteso = oggi - timedelta(days=RITARDO_DATI_GIORNI)

        if not installazione:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_DATA_INSTALLAZIONE: oggi.isoformat()},
            )
            _LOGGER.info(
                "Primo avvio: richiedo la curva del %s per verificare POD e token. "
                "Da domani le richieste partiranno dopo le %s (modificabile dalle "
                "opzioni); per lo storico usa l'azione contatore_letture.recupera_storico.",
                atteso,
                self._orario_richiesta.strftime("%H:%M"),
            )
            return atteso, atteso

        adesso = dt_util.now()
        if adesso.time() < self._orario_richiesta:
            return None

        code = self._leggi_code()
        coda = code.get(pod, {})
        arretrati = sorted(date.fromisoformat(g) for g in coda if date.fromisoformat(g) < atteso)
        if arretrati:
            # Una sola richiesta dal più vecchio arretrato al giorno atteso.
            # Il limite di 150 giorni tiene l'intervallo entro i 6 mesi per
            # richiesta (MAX_MESI_PER_RICHIESTA); in pratica non si attiva quasi
            # mai, perché la coda tiene al massimo MAX_GIORNI_IN_CODA giorni.
            return max(arretrati[0], atteso - timedelta(days=150)), atteso

        ultima_disponibile = await async_get_ultima_data_disponibile(self.hass, pod)
        if ultima_disponibile and ultima_disponibile >= atteso:
            return None

        return atteso, atteso

    async def _async_get_supplies_map(self) -> dict[str, dict]:
        """Restituisce le forniture indicizzate per POD, cachandole in memoria."""
        if self._supplies is None:
            supplies_data = self.entry.data.get(CONF_SUPPLIES)
            if supplies_data:
                self._supplies = dict(supplies_data)
                return self._supplies

            try:
                supplies = await self._api.async_get_supplies()
                self._supplies = {
                    s.get("IdPod", s.get("pod")): s
                    for s in supplies
                    if s.get("IdPod") or s.get("pod")
                }
            except Exception as err:
                _LOGGER.warning(
                    "Impossibile recuperare l'elenco forniture da E-Distribuzione: %s",
                    err,
                )
                self._supplies = {}
        return self._supplies

    async def _async_magnitudes_for_pod(self, pod: str) -> list[str]:
        """Restituisce le grandezze da richiedere per il POD.

        A1 (prelievo) per tutti; A2 (immissione/produzione) solo se HasPlant
        in getSupplies non è False (True per POD di scambio con impianto,
        None/omesso per POD di produzione). Se getSupplies non è disponibile o
        fallisce, per cautela richiede solo A1.
        """
        supplies = await self._async_get_supplies_map()
        supply = supplies.get(pod)
        if supply is not None and supply.get("HasPlant") is not False:
            return ["A1", "A2"]
        return ["A1"]

    # ------------------------------------------------------------------
    # Ciclo di polling automatico
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        await self._async_ensure_token()

        by_pod: dict[str, dict] = {}
        for pod in self.pods:
            richiesta = await self._prossima_richiesta(pod)
            kwh_ultimo_giorno_importato = None
            ultimo_giorno_richiesto = None
            if richiesta is not None:
                data_da, data_a = richiesta
                ultimo_giorno_richiesto = data_a
                try:
                    curva = await self._api.async_get_daily_load_profile(
                        pod, data_da, data_a, magnitude="A1"
                    )
                except EdistribuzioneApiError as err:
                    # I giorni richiesti vanno in coda invece di andare persi:
                    # al ciclo successivo 'atteso' sarebbe già avanzato.
                    for giorno in _giorni_nel_periodo(data_da, data_a):
                        self._accoda_giorno(pod, giorno)
                    raise UpdateFailed(
                        f"Errore chiamando async_get_daily_load_profile per il POD {pod}: {err}"
                    ) from err

                if _curva_ha_dati(curva):
                    await async_import_curva_giornaliera(
                        self.hass, pod, curva, magnitude="A1"
                    )

                    # L'intervallo può tornare parziale (i giorni più recenti
                    # non ancora pubblicati): quelli ricevuti escono dalla
                    # coda, quelli mancanti ci entrano.
                    ricevuti = _giorni_ricevuti(curva)
                    richiesti = _giorni_nel_periodo(data_da, data_a)
                    self._rimuovi_dalla_coda(pod, [g for g in richiesti if g in ricevuti])
                    for giorno in richiesti:
                        if giorno not in ricevuti:
                            self._accoda_giorno(pod, giorno)

                    kwh_ultimo_giorno_importato = _kwh_del_giorno(curva, max(ricevuti)) if ricevuti else None
                else:
                    for giorno in _giorni_nel_periodo(data_da, data_a):
                        self._accoda_giorno(pod, giorno)

                # A2 (immissione/produzione): solo per POD con impianto.
                # Non influenza coda né UpdateFailed: un errore qui genera solo un warning.
                magnitudes = await self._async_magnitudes_for_pod(pod)
                if "A2" in magnitudes:
                    try:
                        curva_a2 = await self._api.async_get_daily_load_profile(
                            pod, data_da, data_a, magnitude="A2"
                        )
                        if _curva_ha_dati(curva_a2):
                            await async_import_curva_giornaliera(
                                self.hass, pod, curva_a2, magnitude="A2"
                            )
                    except EdistribuzioneApiError as err:
                        _LOGGER.warning(
                            "POD %s: errore recuperando A2 per il periodo %s - %s: %s",
                            pod,
                            data_da,
                            data_a,
                            err,
                        )

            # 'reading'/'time_of_use' (letture ufficiali mensili) NON sono
            # piu' recuperati automaticamente qui - lo erano fino al ciclo
            # orario in precedenza, ma nessun sensore li legge dalla
            # riduzione a 3 sensori diagnostici (v0.2.3): erano scaricati
            # ogni ora per niente, solo carico inutile verso le API del
            # distributore. Restano disponibili solo tramite l'API stessa
            # (self.api.async_get_reading/async_get_monthly_time_of_use),
            # da richiamare esplicitamente se in futuro serve esporli.
            ultima_data_disponibile = await async_get_ultima_data_disponibile(self.hass, pod)

            by_pod[pod] = {
                "ultimo_giorno_curva_richiesto": (
                    ultimo_giorno_richiesto.isoformat() if ultimo_giorno_richiesto else None
                ),
                "ultima_data_disponibile": (
                    ultima_data_disponibile.isoformat() if ultima_data_disponibile else None
                ),
                "kwh_ultimo_giorno_importato": kwh_ultimo_giorno_importato,
            }

        return {"by_pod": by_pod}

    # ------------------------------------------------------------------
    # Recupero storico manuale (azione contatore_letture.recupera_storico)
    # ------------------------------------------------------------------

    async def async_recupera_storico(
        self, data_da: date, data_a: date, pod: str | None = None
    ) -> None:
        """Recupera e importa la curva di carico per l'intervallo [data_da, data_a].

        Se 'pod' è omesso, lo fa per TUTTI i POD configurati sulla entry;
        se specificato, solo per quello.

        Solleva HomeAssistantError se al termine non e' stato importato
        nulla: l'azione e' manuale e lanciata dall'interfaccia, dove un
        fallimento silenzioso e' indistinguibile da un successo (vedi issue
        #4). Con piu' POD e un fallimento solo parziale l'azione riesce -
        qualcosa e' stato importato - e i POD falliti restano nei log.

        Una SOLA richiesta per l'intero periodo (per POD): confermato con
        un test reale il 20/08/2026 che l'endpoint restituisce davvero
        tutti i giorni richiesti in un'unica risposta (181 giorni/6 mesi,
        cambio ora legale di marzo incluso e gestito correttamente lato
        server) - non serve più il ciclo giorno per giorno delle versioni
        precedenti.
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

        ultimo_utile = dt_util.now().date() - timedelta(days=RITARDO_DATI_GIORNI)
        if data_a > ultimo_utile:
            raise ServiceValidationError(
                f"La data di fine ({data_a}) è troppo recente: al momento si assume che i "
                f"dati siano disponibili con almeno un giorno di ritardo (non confermato "
                f"con certezza per E-Distribuzione), quindi al massimo fino al {ultimo_utile}."
            )

        # Limiti delle Condizioni Generali di E-Distribuzione: "max 18
        # mesi; 6 mesi per volta" (vedi const.py).
        primo_utile = _stesso_giorno_mesi_prima(dt_util.now().date(), MAX_MESI_STORICO)
        if data_da < primo_utile:
            raise ServiceValidationError(
                f"La data di inizio ({data_da}) è troppo vecchia: E-Distribuzione "
                f"rende disponibili al massimo gli ultimi {MAX_MESI_STORICO} mesi, "
                f"quindi non prima del {primo_utile}."
            )
        inizio_minimo = _stesso_giorno_mesi_prima(data_a, MAX_MESI_PER_RICHIESTA) + timedelta(days=1)
        if data_da < inizio_minimo:
            raise ServiceValidationError(
                f"Il periodo richiesto supera il limite di {MAX_MESI_PER_RICHIESTA} mesi "
                f"per richiesta di E-Distribuzione: con fine al {data_a} l'inizio può "
                f"essere al massimo il {inizio_minimo}. Ripeti l'azione su periodi "
                "consecutivi più corti."
            )

        await self._async_ensure_token()

        _LOGGER.info(
            "Recupero storico E-Distribuzione avviato: %s - %s (POD: %s)",
            data_da,
            data_a,
            ", ".join(pod_da_recuperare),
        )

        # Motivo del fallimento per POD, per poterlo riportare a chi ha
        # lanciato l'azione invece di lasciarlo solo nei log.
        fallimenti: list[str] = []
        pod_con_dati = 0

        for pod_corrente in pod_da_recuperare:
            try:
                curva = await self._api.async_get_daily_load_profile(
                    pod_corrente, data_da, data_a, magnitude="A1"
                )
            except EdistribuzioneApiError as err:
                _LOGGER.warning(
                    "POD %s: errore recuperando il periodo %s - %s: %s",
                    pod_corrente,
                    data_da,
                    data_a,
                    err,
                )
                fallimenti.append(f"{pod_corrente}: {err}")
                continue

            if not curva:
                _LOGGER.warning(
                    "POD %s: nessun dato per l'intero periodo %s - %s", pod_corrente, data_da, data_a
                )
                fallimenti.append(f"{pod_corrente}: nessun dato per il periodo richiesto")
                continue

            await async_import_curva_giornaliera(
                self.hass, pod_corrente, curva, magnitude="A1"
            )

            giorni_ricevuti = {
                g.get("readings", {}).get("sampleDate") for g in curva if _curva_ha_dati([g])
            }
            giorni_attesi = []
            cursore = data_da
            while cursore <= data_a:
                giorni_attesi.append(cursore)
                cursore += timedelta(days=1)

            ricevuti = [g for g in giorni_attesi if g.strftime("%Y%m%d") in giorni_ricevuti]
            mancanti = [g for g in giorni_attesi if g.strftime("%Y%m%d") not in giorni_ricevuti]

            self._rimuovi_dalla_coda(pod_corrente, ricevuti)

            if ricevuti:
                pod_con_dati += 1
            else:
                # La risposta c'era ma nessun giorno conteneva misure: per
                # chi ha lanciato l'azione equivale a non aver importato
                # niente, non a un successo.
                fallimenti.append(
                    f"{pod_corrente}: risposta ricevuta ma senza misure per il periodo"
                )

            dettaglio_mancanti = ""
            if mancanti:
                elenco = ", ".join(g.isoformat() for g in mancanti[:10])
                if len(mancanti) > 10:
                    elenco += f", e altri {len(mancanti) - 10}"
                dettaglio_mancanti = f" ({elenco})"

            _LOGGER.info(
                "POD %s: recupero storico %s - %s completato, %d/%d giorni ricevuti%s",
                pod_corrente,
                data_da,
                data_a,
                len(ricevuti),
                len(giorni_attesi),
                dettaglio_mancanti,
            )

            # A2 (immissione/produzione): solo per POD con impianto.
            magnitudes = await self._async_magnitudes_for_pod(pod_corrente)
            if "A2" in magnitudes:
                try:
                    curva_a2 = await self._api.async_get_daily_load_profile(
                        pod_corrente, data_da, data_a, magnitude="A2"
                    )
                    if curva_a2 and _curva_ha_dati(curva_a2):
                        await async_import_curva_giornaliera(
                            self.hass, pod_corrente, curva_a2, magnitude="A2"
                        )
                except EdistribuzioneApiError as err:
                    _LOGGER.warning(
                        "POD %s: errore recuperando A2 per lo storico %s - %s: %s",
                        pod_corrente,
                        data_da,
                        data_a,
                        err,
                    )

        if pod_con_dati == 0:
            raise HomeAssistantError(
                f"Nessun dato importato per il periodo {data_da} - {data_a}. "
                + "; ".join(fallimenti)
            )

    @property
    def api(self) -> EdistribuzioneApiClient:
        """Expose the API client for on-demand calls."""
        return self._api
