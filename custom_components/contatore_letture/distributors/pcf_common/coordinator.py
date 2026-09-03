"""DataUpdateCoordinator condiviso PCF (Duereti/Unareti).

Logica di polling/coda/retry identica tra i due distributori (verificato
sui rispettivi manuali); le sole differenze sono base_url e display_name,
passati dal chiamante (vedi distributors/duereti.py, distributors/unareti.py).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    PcfApiClient,
    PcfApiError,
    PcfAuthError,
    PcfNotFoundError,
    descrivi_errore,
)
from .const import (
    CONF_DATA_INSTALLAZIONE,
    CONF_PENDING_DATA_A,
    CONF_PENDING_DATA_DA,
    CONF_PENDING_IS_BACKFILL,
    CONF_PENDING_TICKET,
    DEFAULT_SCAN_INTERVAL_HOURS,
    FASE_GIORNALIERO,
    FASE_MANUALE,
    FASE_STORICO,
    CONF_GIORNI_DA_RIPROVARE,
    MAX_DATE_RANGE_MONTHS,
    MAX_GIORNI_IN_CODA,
    MAX_TENTATIVI_PER_GIORNO,
    MINUTI_ATTESA_SUGGERITI,
    MODE_CURVE,
    CONF_ORA_RICHIESTA,
    ORA_MINIMA_RICHIESTA,
    RITARDO_DATI_GIORNI,
)
from ...const import DOMAIN
from .statistics import async_get_ultima_data_disponibile, async_import_curva

_LOGGER = logging.getLogger(__name__)




def _mese_precedente_completo(oggi: date) -> tuple[date, date]:
    """Calcola primo/ultimo giorno del mese precedente a 'oggi'.

    Le curve/misure dei distributori vengono in genere validate e chiuse a
    fine mese, non giorno per giorno: chiedere dati di pochi giorni fa
    (mese ancora in corso) rischia di far restare il job in coda a tempo
    indeterminato perché quei dati semplicemente non esistono ancora.
    """
    primo_giorno_mese_corrente = oggi.replace(day=1)
    ultimo_giorno_mese_precedente = primo_giorno_mese_corrente - timedelta(days=1)
    primo_giorno_mese_precedente = ultimo_giorno_mese_precedente.replace(day=1)
    return primo_giorno_mese_precedente, ultimo_giorno_mese_precedente


def _giorni_nel_periodo(data_da: date, data_a: date) -> list[date]:
    """Elenca i giorni di un periodo, estremi inclusi."""
    giorni = []
    giorno = data_da
    while giorno <= data_a:
        giorni.append(giorno)
        giorno += timedelta(days=1)
    return giorni


def _inizio_n_mesi_prima(fine: date, n_mesi: int) -> date:
    """Primo giorno del mese che inizia n_mesi (incluso il mese di 'fine') prima di 'fine'.

    Es. con fine=2026-07-31 e n_mesi=6 -> 2026-02-01 (Feb...Lug = 6 mesi).
    Con n_mesi=1 -> 2026-07-01, cioè lo stesso range della richiesta mensile
    normale: è il caso limite a cui converge il fallback a range decrescente.
    """
    anno, mese = fine.year, fine.month - (n_mesi - 1)
    while mese <= 0:
        mese += 12
        anno -= 1
    return date(anno, mese, 1)


class PcfCoordinator(DataUpdateCoordinator):
    """Coordina il download giornaliero delle curve e il loro import come statistiche.

    Il ciclo automatico chiede UN SOLO GIORNO per volta (oggi meno
    RITARDO_DATI_GIORNI), dopo l'orario configurato nelle opzioni. I giorni
    per cui il distributore non ha ancora dati finiscono in una coda e
    vengono riprovati nei cicli successivi, dal piu' vecchio, cosi' non si
    creano buchi nello storico.

    Il recupero di periodi passati NON e' automatico: si richiede a mano con
    l'azione contatore_letture.recupera_storico (vedi async_recupera_storico),
    che e' l'unico punto in cui si chiedono intervalli piu' lunghi di un
    giorno.

    Le costanti FASE_GIORNALIERO/FASE_STORICO/FASE_MANUALE non sono fasi di
    una pianificazione automatica: sono etichette che indicano da DOVE
    proviene un import (rispettivamente: ciclo automatico, azione
    recupera_storico, azione recupera_ticket), usate per i sensori
    diagnostici e per riprendere correttamente un ticket dopo un riavvio.

    requestExport è rapida e resta nel ciclo normale del coordinator.
    requestResult può invece restare in coda per ore (polling ogni ~30 minuti,
    vedi const.RESULT_POLL_INTERVAL_SECONDS/MAX_ATTEMPTS): bloccare qui il
    coordinator farebbe fallire il primo setup dell'integrazione, che HA
    considera fallito dopo pochi minuti di attesa. Il polling+import viene
    quindi eseguito in un task in background, disaccoppiato dal ciclo del
    coordinator.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client_id: str,
        secret_id: str,
        pods: list[dict],
        base_url: str,
        display_name: str,
    ) -> None:
        """base_url e display_name identificano il distributore concreto
        (Duereti/Unareti): vedi distributors/duereti.py e
        distributors/unareti.py, che istanziano questa classe passando i
        rispettivi valori. Il resto della logica (polling, coda di retry,
        gestione ticket) e' condivisa e non dipende dal distributore."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({display_name})",
            update_interval=timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS),
        )
        self._entry = entry
        self._pods = pods
        self._display_name = display_name
        session = async_get_clientsession(hass)
        self.api = PcfApiClient(session, client_id, secret_id, base_url)
        self._background_task = None
        self._ultima_richiesta: str | None = None  # chiave del periodo già richiesto
        self.pending_since: datetime | None = None  # per il sensore di stato/attesa
        self.pending_ticket: str | None = None
        # True finché requestToken funziona. Distingue un problema di
        # account da un problema di recupero dati: le entità del
        # dispositivo "Account API" si basano su questo.
        self.token_ok: bool = True

        # Se la entry viene smontata (reload, riavvio, rimozione) mentre il
        # polling in background è a metà, lo cancelliamo invece di lasciarlo
        # orfano. Non perdiamo nulla: il ticket è già persistito sulla entry
        # (vedi _async_update_data), quindi la prossima istanza del
        # coordinator lo riprende comunque in modo pulito.
        entry.async_on_unload(self._annulla_task_in_background)

    @callback
    def _annulla_task_in_background(self) -> None:
        """Cancella il polling in corso quando la entry viene smontata.

        Sicuro da fare: il ticket è già persistito su self._entry.data prima
        che questo task venisse creato, quindi non si perde nulla - una
        nuova istanza del coordinator lo riprenderà al prossimo setup.
        """
        if self._background_task and not self._background_task.done():
            _LOGGER.debug(
                "Smontaggio della entry: annullo il polling in background ancora in corso"
            )
            self._background_task.cancel()

    async def async_forza_ticket(
        self, ticket: str, data_da: date | None = None, data_a: date | None = None
    ) -> None:
        """Riprende manualmente un ticket noto, saltando requestExport.

        Serve quando si è ottenuto un ticket per altre vie (es. una chiamata
        fatta a mano con curl/Bruno, o un ticket che l'integrazione aveva
        perso) e lo si vuole far elaborare senza chiedere a Duereti un nuovo
        export - operazione che il WAF blocca spesso.

        Se le date non vengono indicate si assume il mese precedente completo:
        servono solo come etichetta del periodo nei sensori diagnostici, non
        influenzano i dati, che arrivano interamente dal file.
        """
        if self._background_task and not self._background_task.done():
            # Forzare un ticket è un'azione deliberata dell'utente: ha la
            # precedenza sul recupero in corso, che viene annullato. Logghiamo
            # il ticket interrotto così resta recuperabile dai log (potrebbe
            # essere ancora valido e riutilizzabile in seguito).
            _LOGGER.warning(
                "Annullo il recupero in corso (ticket %s) per forzare il ticket %s",
                self.pending_ticket or "sconosciuto",
                ticket,
            )
            self._background_task.cancel()

        if data_da is None or data_a is None:
            default_da, default_a = _mese_precedente_completo(date.today())
            data_da = data_da or default_da
            data_a = data_a or default_a

        _LOGGER.info(
            "Ticket forzato manualmente: %s (periodo %s - %s)", ticket, data_da, data_a
        )

        nuovi_dati = {
            **self._entry.data,
            CONF_PENDING_TICKET: ticket,
            CONF_PENDING_DATA_DA: data_da.isoformat(),
            CONF_PENDING_DATA_A: data_a.isoformat(),
            CONF_PENDING_IS_BACKFILL: False,
        }
        self.hass.config_entries.async_update_entry(self._entry, data=nuovi_dati)

        self.pending_since = dt_util.utcnow()
        self.pending_ticket = ticket
        self._ultima_richiesta = None  # non è una richiesta nostra: non marcare il periodo
        self._background_task = self.hass.async_create_background_task(
            self._poll_and_import(ticket, data_da, data_a, fase=FASE_MANUALE),
            name=f"{DOMAIN}_poll_import_forzato_{ticket}",
        )

    async def async_recupera_storico(self, data_da: date, data_a: date) -> None:
        """Avvia manualmente il recupero di un periodo storico.

        Il recupero dello storico non è automatico: le API Duereti accettano
        al massimo 6 mesi per richiesta e ogni richiesta può restare in coda
        per ore, quindi è l'utente a decidere quando e quanto recuperare.

        Solleva ServiceValidationError se il periodo non è valido: meglio un
        errore immediato e comprensibile in interfaccia che una richiesta che
        Duereti rifiuterebbe ore dopo.
        """
        if data_da > data_a:
            raise ServiceValidationError(
                f"La data di inizio ({data_da}) è successiva a quella di fine ({data_a})."
            )

        ultimo_utile = date.today() - timedelta(days=RITARDO_DATI_GIORNI)
        if data_a > ultimo_utile:
            raise ServiceValidationError(
                f"La data di fine ({data_a}) è troppo recente: i dati sono disponibili con "
                f"un giorno di ritardo, quindi al massimo fino al {ultimo_utile}."
            )

        limite = _inizio_n_mesi_prima(data_a, MAX_DATE_RANGE_MONTHS)
        if data_da < limite:
            raise ServiceValidationError(
                f"Il periodo richiesto supera il limite di {MAX_DATE_RANGE_MONTHS} mesi "
                f"imposto dalle API {self._display_name}. Con fine {data_a} l'inizio non può essere "
                f"anteriore al {limite}. Per recuperare più storico ripeti l'azione su "
                f"periodi consecutivi."
            )

        if self._background_task and not self._background_task.done():
            _LOGGER.warning(
                "Annullo il recupero in corso (ticket %s) per avviare lo storico %s - %s",
                self.pending_ticket or "sconosciuto",
                data_da,
                data_a,
            )
            self._background_task.cancel()

        _LOGGER.info("Recupero storico avviato manualmente: %s - %s", data_da, data_a)

        # Le due chiamate vengono fatte separatamente per poter distinguere il
        # tipo di problema nel messaggio d'errore: un blocco su requestToken
        # riguarda l'accesso in generale (conviene aspettare), uno su
        # requestExport riguarda questa specifica richiesta (conviene cambiare
        # il periodo). Nessun tentativo automatico: l'azione è manuale, decide
        # l'utente se e quando riprovare.
        try:
            await self.api.async_assicura_token()
        except PcfAuthError as err:
            self.token_ok = False
            raise HomeAssistantError(
                f"Credenziali non valide: {descrivi_errore(err)}"
            ) from err
        except PcfApiError as err:
            self.token_ok = False
            raise HomeAssistantError(
                f"Non è stato possibile autenticarsi su {self._display_name}: {descrivi_errore(err, self._display_name)}. "
                f"Riprova tra {MINUTI_ATTESA_SUGGERITI} minuti."
            ) from err
        else:
            self.token_ok = True

        try:
            ticket = await self.api.request_export(data_da, data_a, self._pods, mode=MODE_CURVE)
        except PcfApiError as err:
            raise HomeAssistantError(
                f"{self._display_name} non ha accettato la richiesta per il periodo {data_da} - {data_a}: "
                f"{descrivi_errore(err)}. L'autenticazione funziona, quindi il problema "
                "riguarda questa richiesta: riprova cambiando le date."
            ) from err

        self.pending_since = dt_util.utcnow()
        self.pending_ticket = ticket
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_PENDING_TICKET: ticket,
                CONF_PENDING_DATA_DA: data_da.isoformat(),
                CONF_PENDING_DATA_A: data_a.isoformat(),
                CONF_PENDING_IS_BACKFILL: FASE_STORICO,
            },
        )
        self._background_task = self.hass.async_create_background_task(
            self._poll_and_import(ticket, data_da, data_a, fase=FASE_STORICO),
            name=f"{DOMAIN}_recupero_storico_{data_da}_{data_a}",
        )

    def _fase_da_entry(self) -> str:
        """Legge la fase del ticket pendente dalla config entry.

        Retrocompatibile con le versioni che salvavano un booleano o nomi di
        fasi non più esistenti: in dubbio si ricade su giornaliero.
        """
        valore = self._entry.data.get(CONF_PENDING_IS_BACKFILL)
        if isinstance(valore, str) and valore in (FASE_GIORNALIERO, FASE_STORICO, FASE_MANUALE):
            return valore
        return FASE_GIORNALIERO

    @property
    def _ora_richiesta(self) -> int:
        """Ora (locale) a partire dalla quale chiedere i dati del giorno prima.

        Configurabile dalle opzioni dell'integrazione: l'orario in cui Duereti
        pubblica i dati non è documentato e può variare, quindi se l'utente
        vede spesso richieste senza dati può spostarlo più avanti.
        """
        valore = self._entry.options.get(CONF_ORA_RICHIESTA)
        if valore is None:
            return ORA_MINIMA_RICHIESTA
        try:
            # Accettiamo anche float o stringa: il selettore numerico
            # restituisce float, e ignorare in silenzio la scelta dell'utente
            # sarebbe peggio che convertirla.
            ora = int(float(valore))
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Ora richiesta non valida nelle opzioni (%r): uso le %d:00",
                valore,
                ORA_MINIMA_RICHIESTA,
            )
            return ORA_MINIMA_RICHIESTA
        if not 0 <= ora <= 23:
            _LOGGER.warning(
                "Ora richiesta fuori intervallo (%r): uso le %d:00", valore, ORA_MINIMA_RICHIESTA
            )
            return ORA_MINIMA_RICHIESTA
        return ora

    def _leggi_coda(self) -> dict[str, int]:
        """Coda dei giorni da riprovare, come {data ISO: tentativi fatti}.

        Persistita sulla config entry: i dati possono arrivare con qualche
        giorno di ritardo, e senza coda un giorno mancato resterebbe un buco
        permanente nello storico.
        """
        coda = self._entry.data.get(CONF_GIORNI_DA_RIPROVARE) or {}
        if isinstance(coda, list):
            # Formato delle prime versioni: solo la lista delle date.
            return {g: 0 for g in coda}
        return dict(coda)

    def _scrivi_coda(self, coda: dict[str, int]) -> None:
        """Salva la coda, scartando i giorni esauriti e limitandone il numero."""
        pulita = {
            giorno: tentativi
            for giorno, tentativi in coda.items()
            if tentativi < MAX_TENTATIVI_PER_GIORNO
        }
        abbandonati = set(coda) - set(pulita)
        if abbandonati:
            _LOGGER.warning(
                "Giorni abbandonati dopo %d tentativi senza dati da %s: %s. "
                "Se servono, richiedili con l'azione contatore_letture.recupera_storico.",
                MAX_TENTATIVI_PER_GIORNO,
                self._display_name,
                ", ".join(sorted(abbandonati)),
            )

        if len(pulita) > MAX_GIORNI_IN_CODA:
            # Teniamo i più recenti: hanno più probabilità di essere prodotti.
            tenuti = sorted(pulita, reverse=True)[:MAX_GIORNI_IN_CODA]
            scartati = set(pulita) - set(tenuti)
            _LOGGER.warning(
                "Coda dei giorni da riprovare oltre %d elementi: scarto i più vecchi (%s)",
                MAX_GIORNI_IN_CODA,
                ", ".join(sorted(scartati)),
            )
            pulita = {g: pulita[g] for g in tenuti}

        if pulita != self._entry.data.get(CONF_GIORNI_DA_RIPROVARE):
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_GIORNI_DA_RIPROVARE: pulita},
            )

    def _accoda_giorno(self, giorno: date) -> None:
        """Mette un giorno in coda, o incrementa i suoi tentativi."""
        coda = self._leggi_coda()
        chiave = giorno.isoformat()
        coda[chiave] = coda.get(chiave, 0) + 1
        _LOGGER.info(
            "Giorno %s senza dati: messo in coda per riprovare (tentativo %d di %d)",
            chiave,
            coda[chiave],
            MAX_TENTATIVI_PER_GIORNO,
        )
        self._scrivi_coda(coda)

    def _rimuovi_dalla_coda(self, giorni: list[date]) -> None:
        """Toglie dalla coda i giorni per cui sono arrivati i dati."""
        coda = self._leggi_coda()
        rimossi = [g.isoformat() for g in giorni if g.isoformat() in coda]
        if not rimossi:
            return
        for chiave in rimossi:
            del coda[chiave]
        _LOGGER.info("Dati ricevuti per %s: rimossi dalla coda", ", ".join(rimossi))
        self._scrivi_coda(coda)

    def _prossima_richiesta(self) -> tuple[str | None, date, date]:
        """Decide se c'è qualcosa da chiedere a Duereti in questo ciclo.

        Si chiede un giorno per volta:

        - al primo avvio dopo l'installazione, subito il giorno atteso
          (oggi-RITARDO_DATI_GIORNI) senza attendere l'orario configurato:
          serve a verificare da subito che POD e dato fiscale siano validi;
        - dai giorni successivi, dopo l'orario configurato, prima si smaltisce
          la coda dei giorni per cui Duereti non aveva ancora dati (dal più
          vecchio, per riempire i buchi in ordine), poi il giorno atteso.

        Il recupero dello storico non è automatico: si avvia a mano con
        l'azione contatore_letture.recupera_storico.

        Restituisce (fase, data_da, data_a); fase è None se non c'è nulla da
        chiedere adesso.
        """
        oggi = date.today()
        installazione = self._entry.data.get(CONF_DATA_INSTALLAZIONE)
        atteso = oggi - timedelta(days=RITARDO_DATI_GIORNI)

        if not installazione:
            # Primo avvio: nessuna attesa. Se POD o dato fiscale sono errati,
            # requestExport risponde subito con un errore di validazione e il
            # problema emerge ora invece che il giorno seguente.
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_DATA_INSTALLAZIONE: oggi.isoformat()},
            )
            _LOGGER.info(
                "Primo avvio: richiedo i dati del %s per verificare POD e dato fiscale. "
                "Da domani le richieste partiranno dopo le %d:00 (modificabile dalle "
                "opzioni); per lo storico usa l'azione contatore_letture.recupera_storico.",
                atteso,
                self._ora_richiesta,
            )
            return FASE_GIORNALIERO, atteso, atteso

        adesso = dt_util.now()
        if adesso.hour < self._ora_richiesta:
            _LOGGER.debug(
                "Sono le %02d:%02d, attendo le %d:00 prima di chiedere i dati",
                adesso.hour,
                adesso.minute,
                self._ora_richiesta,
            )
            return None, oggi, oggi

        # La coda ha la precedenza: i buchi si riempiono dal più vecchio, e i
        # giorni recenti tornerebbero comunque in coda se non fossero pronti.
        coda = self._leggi_coda()
        arretrati = sorted(date.fromisoformat(g) for g in coda if date.fromisoformat(g) < atteso)
        if arretrati:
            # UNA SOLA richiesta che copre dal più vecchio arretrato fino al
            # giorno atteso: le API accettano intervalli (è così che funziona
            # recupera_storico), quindi non serve una chiamata separata per
            # ogni giorno. Conta soprattutto perché ogni richiesta produce un
            # ticket il cui requestResult può restare in coda per ore: un
            # giorno alla volta significherebbe attese seriali.
            #
            # Chiedere anche il giorno atteso, e non solo gli arretrati, evita
            # un buco silenzioso: prima i cicli occupati a smaltire la coda
            # non lo richiedevano affatto, e il giorno dopo 'atteso' era già
            # avanzato - quel giorno non veniva più chiesto da nessuno.
            #
            # L'intervallo può includere giorni già importati (se i buchi non
            # sono contigui): è innocuo, l'import fa merge e ricalcola le
            # somme progressive, si scaricano solo un po' più di dati.
            #
            # Il limite serve a non superare MAX_DATE_RANGE_MONTHS: 150 giorni
            # stanno sempre sotto i 6 mesi calendariali con cui l'API valida
            # l'intervallo. In pratica non si attiva quasi mai, perché la coda
            # tiene al massimo MAX_GIORNI_IN_CODA giorni.
            data_da = max(arretrati[0], atteso - timedelta(days=150))
            _LOGGER.debug(
                "Richiedo %s - %s in un'unica chiamata (%d giorni arretrati in coda)",
                data_da,
                atteso,
                len(arretrati),
            )
            return FASE_GIORNALIERO, data_da, atteso

        return FASE_GIORNALIERO, atteso, atteso

    async def _async_update_data(self) -> dict:
        if self._background_task and not self._background_task.done():
            _LOGGER.debug("Import precedente ancora in corso, salto questo ciclo")
            return await self._con_ultime_date(
                self.data or {"stato": "import precedente ancora in corso"}
            )

        ticket_pendente = self._entry.data.get(CONF_PENDING_TICKET)
        if ticket_pendente:
            # C'era già un ticket ottenuto da un requestExport riuscito prima
            # di un reload/riavvio: lo riprendiamo direttamente invece di
            # rifare requestExport da capo (che rischierebbe di essere
            # bloccato dal WAF proprio mentre Duereti sta già lavorando sul
            # ticket precedente).
            _LOGGER.info("Riprendo il ticket %s salvato da un ciclo precedente", ticket_pendente)
            data_da = date.fromisoformat(self._entry.data[CONF_PENDING_DATA_DA])
            data_a = date.fromisoformat(self._entry.data[CONF_PENDING_DATA_A])
            fase = self._fase_da_entry()

            self.pending_since = dt_util.utcnow()
            self.pending_ticket = ticket_pendente
            self._background_task = self.hass.async_create_background_task(
                self._poll_and_import(ticket_pendente, data_da, data_a, fase),
                name=f"{DOMAIN}_poll_import_ripreso_{ticket_pendente}",
            )
            return await self._con_ultime_date(
                {
                    "stato": "ticket precedente ripreso, in attesa del file",
                    "ticket": ticket_pendente,
                    "periodo": f"{data_da.isoformat()} - {data_a.isoformat()}",
                    "fase": fase,
                }
            )

        fase, data_da, data_a = self._prossima_richiesta()
        if fase is None:
            return await self._con_ultime_date(
                self.data or {"stato": "nessuna richiesta necessaria al momento"}
            )

        chiave = f"{fase}:{data_da.isoformat()}_{data_a.isoformat()}"

        if chiave == self._ultima_richiesta:
            _LOGGER.debug(
                "Periodo %s già richiesto in questa sessione, nessuna nuova richiesta", chiave
            )
            return await self._con_ultime_date(
                self.data or {"stato": f"periodo {chiave} già richiesto"}
            )

        if await self._periodo_gia_coperto(data_a):
            _LOGGER.debug(
                "Periodo %s già coperto dalle statistiche esistenti: nessuna richiesta", chiave
            )
            self._ultima_richiesta = chiave
            return await self._con_ultime_date(
                self.data or {"stato": f"periodo {chiave} già coperto dai dati esistenti"}
            )

        # Prima la chiamata di autenticazione: il suo esito determina lo stato
        # del dispositivo "Account API", distinto da quello dei POD.
        try:
            await self.api.async_assicura_token()
        except PcfAuthError as err:
            self.token_ok = False
            raise ConfigEntryAuthFailed(f"Credenziali non valide: {err}") from err
        except PcfApiError as err:
            self.token_ok = False
            raise UpdateFailed(f"Errore chiamando requestToken: {err}") from err
        else:
            self.token_ok = True

        try:
            ticket = await self.api.request_export(data_da, data_a, self._pods, mode=MODE_CURVE)
        except PcfAuthError as err:
            # Solleva ConfigEntryAuthFailed: HA lo gestisce da solo avviando
            # automaticamente il flusso di reauth con async_step_reauth.
            raise ConfigEntryAuthFailed(f"Credenziali non valide: {err}") from err
        except PcfApiError as err:
            # Se a essere rifiutata e' la richiesta del ciclo giornaliero,
            # TUTTI i giorni coinvolti vanno in coda invece di andare persi:
            # al ciclo successivo 'atteso' sarebbe gia' un altro giorno,
            # lasciando un buco permanente nello storico. Osservato il
            # 02/09/2026 con "errore nelle date inserite" sul giorno
            # precedente - non e' confermato se quel messaggio significhi
            # "dati non ancora pronti" o altro, ma accodare e' comunque la
            # cosa giusta: se il problema e' transitorio i giorni vengono
            # recuperati, se e' permanente la coda si esaurisce da sola
            # dopo MAX_TENTATIVI_PER_GIORNO.
            #
            # Il ciclo giornaliero puo' ora chiedere un INTERVALLO (vedi
            # _prossima_richiesta), non piu' un giorno solo: per questo si
            # itera sul periodo invece di controllare data_da == data_a.
            if fase == FASE_GIORNALIERO:
                for giorno in _giorni_nel_periodo(data_da, data_a):
                    self._accoda_giorno(giorno)
            raise UpdateFailed(f"Errore chiamando requestExport: {err}") from err

        self._ultima_richiesta = chiave
        self.pending_since = dt_util.utcnow()
        self.pending_ticket = ticket

        nuovi_dati = {
            **self._entry.data,
            CONF_PENDING_TICKET: ticket,
            CONF_PENDING_DATA_DA: data_da.isoformat(),
            CONF_PENDING_DATA_A: data_a.isoformat(),
            CONF_PENDING_IS_BACKFILL: fase,
        }
        self.hass.config_entries.async_update_entry(self._entry, data=nuovi_dati)

        self._background_task = self.hass.async_create_background_task(
            self._poll_and_import(ticket, data_da, data_a, fase),
            name=f"{DOMAIN}_poll_import_{chiave}",
        )

        return await self._con_ultime_date(
            {
                "stato": "richiesta inviata, in attesa del file",
                "ticket": ticket,
                "periodo": f"{data_da.isoformat()} - {data_a.isoformat()}",
                "fase": fase,
            }
        )

    async def _periodo_gia_coperto(self, data_a: date) -> bool:
        """True se TUTTI i POD configurati hanno già dati persistenti (nelle
        external statistics) che coprono almeno fino a 'data_a'.

        Usa lo stesso stato che legge il sensore 'Ultima data disponibile',
        quindi sopravvive a reload/riavvii - a differenza di
        self._ultima_richiesta, che è solo in memoria.
        """
        if not self._pods:
            return False
        for pod_conf in self._pods:
            data_disp = await async_get_ultima_data_disponibile(self.hass, pod_conf["pod"])
            if data_disp is None or data_disp < data_a:
                return False
        return True

    async def _con_ultime_date(self, dati: dict) -> dict:
        """Aggiunge al dict 'ultime_date_per_pod' leggendo lo stato reale
        delle external statistics, indipendentemente da nuove richieste.

        Se per un POD il database non restituisce nulla ma avevamo già un
        valore noto, quest'ultimo viene conservato: la scrittura delle
        statistiche passa dal recorder in modo asincrono, quindi una lettura
        può temporaneamente non vedere dati appena importati e non deve
        riportare il sensore a "Sconosciuto".
        """
        note = (self.data or {}).get("ultime_date_per_pod", {})
        ultime_date = dict(note)
        for pod_conf in self._pods:
            pod = pod_conf["pod"]
            data_disp = await async_get_ultima_data_disponibile(self.hass, pod)
            if data_disp is not None:
                ultime_date[pod] = data_disp.isoformat()
        return {**dati, "ultime_date_per_pod": ultime_date}

    def _avvia_reauth(self) -> None:
        """Avvia il flusso di reauth manualmente: serve perché questo viene
        chiamato dal task in background (_poll_and_import), che non passa
        dal ciclo normale del coordinator e quindi non beneficia della
        gestione automatica di ConfigEntryAuthFailed."""
        if hasattr(self._entry, "async_start_reauth"):
            self._entry.async_start_reauth(self.hass)
        else:  # fallback per versioni HA più datate
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={
                        "source": config_entries.SOURCE_REAUTH,
                        "entry_id": self._entry.entry_id,
                    },
                    data=self._entry.data,
                )
            )

    def _pulisci_ticket_pendente(self) -> None:
        """Rimuove il ticket persistito dalla config entry: usato quando il
        polling è finito (successo o errore definitivo), così un eventuale
        reload successivo non lo trovi più e non tenti di riprenderlo."""
        if CONF_PENDING_TICKET not in self._entry.data:
            return
        nuovi_dati = dict(self._entry.data)
        nuovi_dati.pop(CONF_PENDING_TICKET, None)
        nuovi_dati.pop(CONF_PENDING_DATA_DA, None)
        nuovi_dati.pop(CONF_PENDING_DATA_A, None)
        nuovi_dati.pop(CONF_PENDING_IS_BACKFILL, None)
        self.hass.config_entries.async_update_entry(self._entry, data=nuovi_dati)

    async def _poll_and_import(
        self, ticket: str, data_da: date, data_a: date, fase: str
    ) -> None:
        """Task in background: aspetta il file (anche per ore) e importa i dati."""
        try:
            zip_bytes = await self.api.request_result(ticket)
        except asyncio.CancelledError:
            # Il task è stato annullato di proposito (reload della entry, o
            # un'azione manuale che ha la precedenza). Non è un errore: il
            # ticket resta salvato e verrà ripreso. CancelledError non deriva
            # da Exception, quindi senza questo ramo sfuggirebbe alla gestione
            # sotto lasciando pending_since valorizzato per sempre.
            _LOGGER.debug("Polling del ticket %s annullato", ticket)
            self.pending_since = None
            self.pending_ticket = None
            raise
        except PcfAuthError as err:
            # Le credenziali non c'entrano con la validità del ticket:
            # lo conserviamo, così dopo il reauth si riprende da lì.
            _LOGGER.error(
                "Credenziali non valide durante il polling: %s. Il ticket %s viene conservato "
                "e verrà ripreso dopo il reinserimento delle credenziali.",
                err,
                ticket,
            )
            self.pending_since = None
            self.pending_ticket = None
            self._avvia_reauth()
            dati = await self._con_ultime_date(
                {
                    **(self.data or {}),
                    "stato": f"credenziali non valide: {err}",
                    "ultimo_errore": str(err),
                    "ticket_conservato": ticket,
                }
            )
            self.async_set_updated_data(dati)
            return
        except PcfNotFoundError as err:
            # Unico caso in cui ha senso scartare il ticket: Duereti dice
            # esplicitamente che non è collegato a nessun dato.
            _LOGGER.error("Ticket %s non valido lato %s: %s", ticket, self._display_name, err)
            self.pending_since = None
            self.pending_ticket = None
            self._pulisci_ticket_pendente()
            if fase == FASE_GIORNALIERO and "dat" in str(err).lower():
                # "Nessun dato trovato": il periodo non è (ancora) disponibile.
                # Lo mettiamo in coda invece di perderlo.
                for giorno in _giorni_nel_periodo(data_da, data_a):
                    self._accoda_giorno(giorno)
            dati = await self._con_ultime_date(
                {**(self.data or {}), "stato": f"ticket non valido: {err}", "ultimo_errore": str(err)}
            )
            self.async_set_updated_data(dati)
            return
        except PcfApiError as err:
            # Timeout del polling, blocco WAF, errore di rete: il ticket lato
            # Duereti resta valido, quindi lo CONSERVIAMO e al prossimo ciclo
            # riprendiamo da lì invece di richiederne uno nuovo.
            _LOGGER.error(
                "Errore recuperando il file per il ticket %s: %s. Il ticket viene conservato "
                "per riprovare al prossimo ciclo.",
                ticket,
                err,
            )
            self.pending_since = None
            self.pending_ticket = None
            dati = await self._con_ultime_date(
                {
                    **(self.data or {}),
                    "stato": f"errore: {err}",
                    "ultimo_errore": str(err),
                    "ticket_conservato": ticket,
                }
            )
            self.async_set_updated_data(dati)
            return

        # Da qui in poi il file è stato ricevuto: qualunque errore in
        # decodifica/parsing/import va gestito, altrimenti l'eccezione esce dal
        # task in background lasciando pending_since valorizzato per sempre
        # (il sensore "Attesa file" continuerebbe a salire all'infinito pur
        # avendo già ricevuto i dati) e il ticket persistito non ripulito.
        try:
            from .api import parse_curve_zip

            _LOGGER.debug(
                "File ricevuto per il ticket %s (%d byte), avvio parsing", ticket, len(zip_bytes)
            )
            risultati = parse_curve_zip(zip_bytes)
            _LOGGER.debug(
                "Parsing completato: %d POD trovati nel file (%s)",
                len(risultati),
                ", ".join(f"{pod}: {len(r.punti)} punti" for pod, r in risultati.items()) or "nessuno",
            )

            totali_kwh = {
                pod: round(sum(p.valore_kwh for p in ris.punti), 3) for pod, ris in risultati.items()
            }

            date_importate: dict[str, str] = {}
            for pod_conf in self._pods:
                pod = pod_conf["pod"]
                risultato = risultati.get(pod)
                if risultato is None:
                    _LOGGER.warning(
                        "Nessun dato ricevuto per POD %s (periodo %s - %s). POD presenti nel "
                        "file: %s",
                        pod,
                        data_da,
                        data_a,
                        list(risultati.keys()) or "nessuno",
                    )
                    continue
                _LOGGER.debug("Importo %d punti per il POD %s", len(risultato.punti), pod)
                ultima = await async_import_curva(
                    self.hass, pod, risultato, distributor_display_name=self._display_name
                )
                if ultima is not None:
                    date_importate[pod] = ultima.isoformat()

            # Confrontiamo i giorni richiesti con quelli effettivamente
            # presenti nel file: Duereti può restituire un file valido ma
            # incompleto (i dati più recenti non ancora pubblicati). I giorni
            # ottenuti escono dalla coda, quelli mancanti ci entrano.
            if fase == FASE_GIORNALIERO:
                ricevuti = {
                    punto.timestamp.date()
                    for risultato in risultati.values()
                    for punto in risultato.punti
                }
                richiesti = _giorni_nel_periodo(data_da, data_a)
                self._rimuovi_dalla_coda([g for g in richiesti if g in ricevuti])
                for giorno in richiesti:
                    if giorno not in ricevuti:
                        self._accoda_giorno(giorno)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception(
                "Errore elaborando il file ricevuto per il ticket %s: %s. Il ticket viene "
                "CONSERVATO: il file lato Duereti è valido, il problema è nell'elaborazione "
                "locale, quindi al prossimo ciclo verrà riscaricato con lo stesso ticket "
                "invece di sprecarne uno nuovo (che il WAF potrebbe bloccare).",
                ticket,
                err,
            )
            # Volutamente NON chiamiamo _pulisci_ticket_pendente(): un errore
            # qui riguarda il nostro codice (parsing, import, formato dei dati),
            # non la validità del ticket. Ripulirlo significherebbe buttare via
            # un ticket funzionante e doverne richiedere un altro, cosa tutt'altro
            # che gratuita visti i blocchi intermittenti del WAF su requestExport.
            self.pending_since = None
            self.pending_ticket = None
            dati = await self._con_ultime_date(
                {
                    **(self.data or {}),
                    "stato": f"errore elaborando il file: {err}",
                    "ultimo_errore": str(err),
                    "ticket_conservato": ticket,
                }
            )
            self.async_set_updated_data(dati)
            return

        self.pending_since = None
        self.pending_ticket = None
        self._pulisci_ticket_pendente()

        dati = await self._con_ultime_date(
            {
                "stato": "ok",
                "ultimo_aggiornamento": data_a.isoformat(),
                "periodo_importato": f"{data_da.isoformat()} - {data_a.isoformat()}",
                "pod_aggiornati": list(risultati.keys()),
                "fase": fase,
                "totale_kwh_periodo_per_pod": totali_kwh,
            }
        )
        # Le date appena importate hanno la precedenza su quelle rilette dal
        # database: async_add_external_statistics accoda la scrittura al
        # recorder, quindi una rilettura immediata non le vedrebbe ancora e il
        # sensore "Ultima data disponibile" resterebbe a "Sconosciuto" fino al
        # ciclo successivo (24 ore dopo).
        dati["ultime_date_per_pod"] = {
            **dati.get("ultime_date_per_pod", {}),
            **date_importate,
        }
        # async_set_updated_data segna anche l'aggiornamento come riuscito:
        # serve perché l'import avviene in un task separato dal ciclo del
        # coordinator, che potrebbe essere rimasto marcato come fallito (per
        # esempio dopo la cancellazione del task precedente da parte di
        # un'azione manuale). Senza, le entità del POD resterebbero non
        # disponibili nonostante l'import sia andato a buon fine.
        self.async_set_updated_data(dati)
        _LOGGER.debug(
            "Import completato: stato del coordinator aggiornato a riuscito (%d POD)",
            len(date_importate),
        )
