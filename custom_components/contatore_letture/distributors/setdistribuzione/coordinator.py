"""DataUpdateCoordinator per SET Distribuzione.

Design (il "perché" in documentation/protocols/set-distribuzione-protocol.md):

  - La curva con i quarti d'ora si chiede un giorno per richiesta
    (consumption con 'month' e 'day'). Ogni ciclo chiede il giorno atteso
    (oggi - RITARDO_DATI_GIORNI), se non è già nelle statistiche, più i
    giorni rimasti in coda. Stessa coda per-POD di edistribuzione/ireti: i
    giorni ricevuti escono, quelli mancanti o incompleti entrano e vengono
    riprovati, abbandonati con un avviso dopo ABBANDONO_CODA_DOPO_GIORNI.

  - Orario della richiesta scelto a caso per installazione tra le 18 e le
    22 (come edistribuzione tra le 16 e le 20), salvato nelle opzioni. Il
    primo ciclo dopo la configurazione chiede subito ieri, per verificare
    login e POD; da lì in poi si chiede solo dopo l'orario.

  - Il login (B2C, 4 richieste) si fa solo nei cicli che hanno davvero un
    giorno da chiedere: il ciclo gira ogni ora, ma prima dell'orario o con
    ieri già importato non tocca il portale. Nessun token conservato (refresh_token da 24 ore non
    rinnovabile, vedi const.py). Credenziali non valide sollevano
    ConfigEntryAuthFailed (HA avvia il reauth).

  - fiscalCode, profilo e contractId necessari ai path si rileggono dopo
    ogni login (2 richieste): un cambio di contratto sullo stesso POD non
    richiede di riconfigurare l'integrazione.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

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
from .api import SetDistribuzioneApiClient, SetDistribuzioneApiError
from .auth import (
    SetDistribuzioneAuthClient,
    SetDistribuzioneAuthError,
    SetDistribuzioneInvalidCredentials,
    nuovo_cookie_jar,
)
from .const import (
    ABBANDONO_CODA_DOPO_GIORNI,
    CONF_DATA_INSTALLAZIONE,
    CONF_EMAIL,
    CONF_GIORNI_DA_RIPROVARE,
    CONF_ORARIO_RICHIESTA,
    CONF_PASSWORD,
    CONF_PODS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    MAX_GIORNI_IN_CODA,
    MAX_GIORNI_RECUPERO_STORICO,
    RITARDO_DATI_GIORNI,
    orario_casuale,
    orario_da_opzioni,
)
from .statistics import async_get_ultima_data_disponibile, async_import_ore, ore_del_giorno

_LOGGER = logging.getLogger(__name__)

# recupera_storico scrive le statistiche a blocchi di questa dimensione
# invece che una volta per giorno: ogni scrittura rilegge e riscrive
# l'intera serie del POD (statistics_common).
_GIORNI_PER_BLOCCO_RECUPERO = 31


def _giorni_nel_periodo(data_da: date, data_a: date) -> list[date]:
    """Elenco dei giorni compresi nell'intervallo, estremi inclusi."""
    giorni, cursore = [], data_da
    while cursore <= data_a:
        giorni.append(cursore)
        cursore += timedelta(days=1)
    return giorni


class SetDistribuzioneCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} (SET Distribuzione)",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
            config_entry=entry,
        )
        self.entry = entry
        self.pods: list[str] = list(entry.data[CONF_PODS])
        # Sessione con cookie jar proprio, non quotato: vedi nuovo_cookie_jar.
        self._session = async_create_clientsession(hass, cookie_jar=nuovo_cookie_jar())
        self._auth = SetDistribuzioneAuthClient(self._session)
        self._orario_ripiego: time | None = None
        self._assicura_orario_richiesta()

    def _assicura_orario_richiesta(self) -> None:
        """Entry senza orario: ne sceglie uno casuale e lo salva nelle
        opzioni, così resta lo stesso ai riavvii (come edistribuzione)."""
        if CONF_ORARIO_RICHIESTA in self.entry.options:
            return
        orario = orario_casuale()
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, CONF_ORARIO_RICHIESTA: orario}
        )
        _LOGGER.info("Orario della richiesta giornaliera scelto a caso: %s", orario)

    @property
    def _orario_richiesta(self) -> time:
        """Orario (locale) da cui chiedere il giorno precedente."""
        orario = orario_da_opzioni(self.entry.options)
        if orario is not None:
            return orario
        # Valore rovinato: uno casuale per questo avvio, senza salvarlo.
        if self._orario_ripiego is None:
            self._orario_ripiego = time.fromisoformat(orario_casuale())
            _LOGGER.warning(
                "Orario richiesta non valido nelle opzioni (%r): uso le %s",
                self.entry.options.get(CONF_ORARIO_RICHIESTA),
                self._orario_ripiego.strftime("%H:%M"),
            )
        return self._orario_ripiego

    def _e_ora_di_chiedere(self) -> bool:
        """Al primo avvio subito (verifica login e POD), poi solo dopo
        l'orario della richiesta."""
        if not self.entry.data.get(CONF_DATA_INSTALLAZIONE):
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CONF_DATA_INSTALLAZIONE: dt_util.now().date().isoformat(),
                },
            )
            _LOGGER.info(
                "Primo avvio: richiedo subito il giorno precedente per verificare login "
                "e POD. Da domani le richieste partiranno dopo le %s; per lo storico "
                "usa l'azione contatore_letture.recupera_storico.",
                self._orario_richiesta.strftime("%H:%M"),
            )
            return True
        return dt_util.now().time() >= self._orario_richiesta

    async def _async_login(self) -> SetDistribuzioneApiClient:
        try:
            access_token = await self._auth.async_login(
                self.entry.data[CONF_EMAIL], self.entry.data[CONF_PASSWORD]
            )
        except SetDistribuzioneInvalidCredentials as err:
            raise ConfigEntryAuthFailed(f"Credenziali SET Distribuzione non valide: {err}") from err
        except SetDistribuzioneAuthError as err:
            raise UpdateFailed(f"Login SET Distribuzione fallito: {err}") from err
        return SetDistribuzioneApiClient(self._session, access_token)

    @staticmethod
    async def _async_riferimenti(api: SetDistribuzioneApiClient) -> dict[str, dict[str, str]]:
        try:
            return {p["pod"]: p for p in await api.async_get_pod_account()}
        except SetDistribuzioneApiError as err:
            raise UpdateFailed(f"Errore leggendo le forniture SET Distribuzione: {err}") from err

    @staticmethod
    async def _async_scarica_giorno(
        api: SetDistribuzioneApiClient, rif: dict[str, str], giorno: date
    ) -> dict[datetime, float] | None:
        consumi = await api.async_get_consumi_giorno(
            rif["fiscal_code"], rif["profile"], rif["contract_id"], rif["pod"], giorno
        )
        return ore_del_giorno(consumi, giorno)

    # ------------------------------------------------------------------
    # Coda dei giorni da riprovare, PER POD (stesso meccanismo di
    # edistribuzione/ireti - vedi edistribuzione/coordinator.py per i
    # commenti estesi sul formato, qui non duplicati).
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
                    "SET Distribuzione: %s. Se servono, richiedili con l'azione "
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
                "POD %s: giorno %s ancora senza dati da SET Distribuzione, in coda da "
                "%d giorni (max %d)",
                pod,
                chiave,
                (dt_util.now().date() - coda[chiave]).days,
                ABBANDONO_CODA_DOPO_GIORNI,
            )
        else:
            coda[chiave] = dt_util.now().date()
            _LOGGER.info(
                "POD %s: giorno %s senza dati da SET Distribuzione, messo in coda per "
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

    async def _giorni_da_chiedere(self, pod: str) -> list[date]:
        """Giorni da chiedere in questo ciclo: quelli in coda più il giorno
        atteso, se non è già nelle statistiche. Lista vuota = niente da
        fare (e niente login)."""
        atteso = dt_util.now().date() - timedelta(days=RITARDO_DATI_GIORNI)
        coda = self._leggi_code().get(pod, {})
        giorni = {date.fromisoformat(g) for g in coda if date.fromisoformat(g) < atteso}

        ultima_disponibile = await async_get_ultima_data_disponibile(self.hass, pod)
        if not ultima_disponibile or ultima_disponibile < atteso:
            giorni.add(atteso)
        return sorted(giorni)

    # ------------------------------------------------------------------
    # Ciclo di polling automatico
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        if self._e_ora_di_chiedere():
            richieste = {pod: await self._giorni_da_chiedere(pod) for pod in self.pods}
        else:
            richieste = {pod: [] for pod in self.pods}
        riferimenti: dict[str, dict[str, str]] = {}
        api = None
        if any(richieste.values()):
            api = await self._async_login()
            riferimenti = await self._async_riferimenti(api)

        precedenti = (self.data or {}).get("by_pod", {})
        by_pod: dict[str, dict] = {}
        for pod in self.pods:
            giorni = richieste[pod]
            dati_pod = dict(precedenti.get(pod) or {
                "ultimo_giorno_importato": None,
                "kwh_ultimo_giorno_importato": None,
            })

            if giorni:
                rif = riferimenti.get(pod)
                if rif is None:
                    for giorno in giorni:
                        self._accoda_giorno(pod, giorno)
                    raise UpdateFailed(
                        f"Il POD {pod} non risulta più tra le forniture elettriche "
                        "dell'account mySET"
                    )

                ricevuti: dict[date, dict[datetime, float]] = {}
                for giorno in giorni:
                    try:
                        ore = await self._async_scarica_giorno(api, rif, giorno)
                    except SetDistribuzioneApiError as err:
                        # Prima di uscire si salva quello che è arrivato e si
                        # accoda il resto, così nessun giorno va perso.
                        await self._async_salva(pod, ricevuti, giorni)
                        raise UpdateFailed(
                            f"Errore recuperando i consumi SET Distribuzione del {giorno} "
                            f"per il POD {pod}: {err}"
                        ) from err
                    if ore is not None:
                        ricevuti[giorno] = ore

                await self._async_salva(pod, ricevuti, giorni)
                if ricevuti:
                    ultimo = max(ricevuti)
                    dati_pod["ultimo_giorno_importato"] = ultimo.isoformat()
                    dati_pod["kwh_ultimo_giorno_importato"] = sum(ricevuti[ultimo].values())

            ultima_data_disponibile = await async_get_ultima_data_disponibile(self.hass, pod)
            dati_pod["ultima_data_disponibile"] = (
                ultima_data_disponibile.isoformat() if ultima_data_disponibile else None
            )
            by_pod[pod] = dati_pod

        return {"by_pod": by_pod}

    async def _async_salva(
        self, pod: str, ricevuti: dict[date, dict[datetime, float]], richiesti: list[date]
    ) -> None:
        """Importa i giorni ricevuti e aggiorna la coda: escono quelli
        arrivati, entrano i richiesti che non lo sono."""
        ore: dict[datetime, float] = {}
        for ore_giorno in ricevuti.values():
            ore.update(ore_giorno)
        if ore:
            await async_import_ore(self.hass, pod, ore)
        self._rimuovi_dalla_coda(pod, sorted(ricevuti))
        for giorno in richiesti:
            if giorno not in ricevuti:
                self._accoda_giorno(pod, giorno)

    # ------------------------------------------------------------------
    # Recupero storico manuale (azione contatore_letture.recupera_storico)
    # ------------------------------------------------------------------

    async def async_recupera_storico(
        self, data_da: date, data_a: date, pod: str | None = None
    ) -> None:
        """Recupera e importa la curva oraria per [data_da, data_a], per
        tutti i POD della entry o solo per 'pod'. Una richiesta per giorno,
        statistiche scritte a blocchi di _GIORNI_PER_BLOCCO_RECUPERO giorni.
        I giorni recuperati escono anche dalla coda del ciclo automatico.

        Solleva HomeAssistantError se al termine non è stato importato
        nessun giorno (stesso contratto degli altri distributori, issue #4).
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
                f"richiesta (limite di cortesia: {MAX_GIORNI_RECUPERO_STORICO} giorni, "
                "una richiesta al portale per ogni giorno). Ripeti l'azione su periodi "
                "più corti."
            )

        api = await self._async_login()
        riferimenti = await self._async_riferimenti(api)
        giorni = _giorni_nel_periodo(data_da, data_a)
        blocchi = [
            giorni[i:i + _GIORNI_PER_BLOCCO_RECUPERO]
            for i in range(0, len(giorni), _GIORNI_PER_BLOCCO_RECUPERO)
        ]

        _LOGGER.info(
            "Recupero storico SET Distribuzione avviato: %s - %s (%d giorni, POD: %s)",
            data_da, data_a, giorni_totali, ", ".join(pod_da_recuperare),
        )

        fallimenti: list[str] = []
        giorni_importati = 0

        for pod_corrente in pod_da_recuperare:
            rif = riferimenti.get(pod_corrente)
            if rif is None:
                fallimenti.append(
                    f"{pod_corrente}: non risulta tra le forniture elettriche dell'account"
                )
                continue

            trovati = 0
            for blocco in blocchi:
                ricevuti: dict[date, dict[datetime, float]] = {}
                for giorno in blocco:
                    try:
                        ore = await self._async_scarica_giorno(api, rif, giorno)
                    except SetDistribuzioneApiError as err:
                        _LOGGER.warning("POD %s: errore recuperando il %s: %s", pod_corrente, giorno, err)
                        fallimenti.append(f"{pod_corrente}/{giorno}: {err}")
                        continue
                    if ore is not None:
                        ricevuti[giorno] = ore

                if ricevuti:
                    ore_blocco: dict[datetime, float] = {}
                    for ore_giorno in ricevuti.values():
                        ore_blocco.update(ore_giorno)
                    await async_import_ore(self.hass, pod_corrente, ore_blocco)
                    self._rimuovi_dalla_coda(pod_corrente, sorted(ricevuti))
                trovati += len(ricevuti)

            giorni_importati += trovati
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
