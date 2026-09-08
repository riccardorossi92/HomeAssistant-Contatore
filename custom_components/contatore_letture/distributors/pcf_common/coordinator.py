"""DataUpdateCoordinator condiviso PCF (Duereti/Unareti).

Logica identica tra i due distributori (verificato sui rispettivi
manuali); le sole differenze sono base_url e display_name, passati dal
chiamante (vedi distributors/duereti.py, distributors/unareti.py).

Modello dati: MESE SOLARE CHIUSO, con un CURSORE PERSISTITO PER POD.
Dal 08/09/2026 i manuali dichiarano che le CURVE sono disponibili "solo
fino al mese appena concluso" e non "relative al mese corrente": il dato
non è più giornaliero. Non essendoci un ritardo fisso da cui dedurre
quale mese chiedere ad ogni ciclo, il coordinator tiene su entry.data un
cursore {pod: "YYYY-MM"} = prossimo mese da importare per quel POD, e lo
avanza solo quando quel mese è stato importato con successo (stesso
schema del coordinator Areti). Un mese mai pubblicato blocca il cursore
di quel POD finché non arriva: nessun abbandono automatico, l'escape è
l'azione contatore_letture.recupera_storico.
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

from ...const import DOMAIN
from .api import (
    PcfApiClient,
    PcfApiError,
    PcfAuthError,
    PcfNotFoundError,
    descrivi_errore,
    parse_curve_zip,
)
from .const import (
    CONF_MESE_DA_IMPORTARE,
    CONF_PENDING_DATA_A,
    CONF_PENDING_DATA_DA,
    CONF_PENDING_IS_BACKFILL,
    CONF_PENDING_TICKET,
    DEFAULT_SCAN_INTERVAL_HOURS,
    FASE_AUTOMATICA,
    FASE_MANUALE,
    FASE_STORICO,
    MAX_ANNI_STORICO,
    MAX_DATE_RANGE_MONTHS,
    MINUTI_ATTESA_SUGGERITI,
    MODE_CURVE,
)
from .date_utils import (
    mese_e_chiuso,
    mese_precedente_completo,
    mese_str,
    mese_successivo,
    primo_giorno_mese,
    ultimo_giorno_mese,
    ultimo_mese_chiuso,
)
from .statistics import async_get_ultima_data_disponibile, async_import_curva

_LOGGER = logging.getLogger(__name__)

# Chiavi di entry.data lasciate dalle versioni "a giorno" (coda dei giorni
# da riprovare, data di installazione): non più lette da nessuno, vengono
# rimosse al primo ciclo dopo l'aggiornamento per non lasciare stato morto.
_CHIAVI_OBSOLETE = ("giorni_da_riprovare", "data_installazione")


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
    """Coordina il download mensile delle curve e il loro import come statistiche.

    Il ciclo automatico, una volta al giorno (la granularità utile è
    mensile), per ciascun POD guarda il suo cursore 'mese_da_importare':

    - se punta a un mese GIÀ CHIUSO, il coordinator sceglie il più vecchio
      tra tutti i cursori arretrati e chiede quel mese per il gruppo di POD
      che lo condividono (una sola requestExport per ciclo: c'è un solo
      slot per il ticket pendente, e ogni requestResult può restare in coda
      per ore);
    - quando il file arriva, ogni POD per cui contiene dati avanza il suo
      cursore al mese successivo; gli altri restano fermi e vengono
      riprovati al ciclo dopo. Nessun abbandono automatico.
    - primo avvio (o POD aggiunto dopo): il cursore parte dal mese corrente,
      nessun backfill automatico. Per lo storico c'è
      contatore_letture.recupera_storico.

    Le costanti FASE_AUTOMATICA/FASE_STORICO/FASE_MANUALE non sono fasi di
    una pianificazione: sono etichette che indicano da DOVE proviene un
    import (ciclo automatico, azione recupera_storico, azione
    recupera_ticket), usate per i sensori diagnostici, per riprendere
    correttamente un ticket dopo un riavvio e per decidere se avanzare i
    cursori (solo FASE_AUTOMATICA li tocca).

    requestExport è rapida e resta nel ciclo normale del coordinator.
    requestResult può invece restare in coda per ore (polling ogni ~30
    minuti, vedi const.RESULT_POLL_INTERVAL_SECONDS/MAX_ATTEMPTS): bloccare
    qui il coordinator farebbe fallire il primo setup dell'integrazione,
    che HA considera fallito dopo pochi minuti. Il polling+import viene
    quindi eseguito in un task in background, disaccoppiato dal ciclo.
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
        rispettivi valori. Il resto della logica (cursori, gestione ticket,
        polling in background) è condivisa e non dipende dal distributore."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({display_name})",
            update_interval=timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS),
            config_entry=entry,
        )
        self._entry = entry
        self._pods = pods
        self._display_name = display_name
        session = async_get_clientsession(hass)
        self.api = PcfApiClient(session, client_id, secret_id, base_url)
        self._background_task = None
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

    # ------------------------------------------------------------------
    # Cursore mensile persistito, per POD
    # ------------------------------------------------------------------

    def _cursori(self) -> dict[str, str]:
        """{pod: "YYYY-MM"} = prossimo mese da importare per ogni POD
        configurato. I POD non ancora presenti (primo avvio, o POD aggiunto
        dalle opzioni) vengono inizializzati al MESE CORRENTE - nessun
        backfill automatico, esattamente come il coordinator Areti - e la
        modifica viene persistita subito.
        """
        salvati = dict(self._entry.data.get(CONF_MESE_DA_IMPORTARE) or {})
        mese_corrente = mese_str(dt_util.now().date())
        codici = [p["pod"] for p in self._pods]
        # Solo i POD configurati adesso: inizializza i mancanti al mese
        # corrente e lascia cadere eventuali cursori di POD rimossi.
        aggiornato = {pod: salvati.get(pod, mese_corrente) for pod in codici}
        if aggiornato != salvati:
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_MESE_DA_IMPORTARE: aggiornato},
            )
        return aggiornato

    def _scrivi_cursore(self, pod: str, mese: str) -> None:
        cursori = dict(self._entry.data.get(CONF_MESE_DA_IMPORTARE) or {})
        if cursori.get(pod) == mese:
            return
        cursori[pod] = mese
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, CONF_MESE_DA_IMPORTARE: cursori},
        )

    def _avanza_cursore(self, pod: str) -> None:
        """Porta il cursore di un POD al mese successivo a quello attuale."""
        cursori = self._entry.data.get(CONF_MESE_DA_IMPORTARE) or {}
        attuale = cursori.get(pod)
        if attuale is None:
            return
        nuovo = mese_successivo(attuale)
        self._scrivi_cursore(pod, nuovo)
        _LOGGER.info("POD %s: mese %s importato, cursore avanzato a %s", pod, attuale, nuovo)

    def _pulisci_chiavi_obsolete(self) -> None:
        """Rimuove da entry.data lo stato lasciato dalle versioni 'a giorno'."""
        if not any(k in self._entry.data for k in _CHIAVI_OBSOLETE):
            return
        nuovi = {k: v for k, v in self._entry.data.items() if k not in _CHIAVI_OBSOLETE}
        self.hass.config_entries.async_update_entry(self._entry, data=nuovi)

    async def async_forza_ticket(
        self, ticket: str, data_da: date | None = None, data_a: date | None = None
    ) -> None:
        """Riprende manualmente un ticket noto, saltando requestExport.

        Serve quando si è ottenuto un ticket per altre vie (es. una chiamata
        fatta a mano con curl/Bruno, o un ticket che l'integrazione aveva
        perso) e lo si vuole far elaborare senza chiedere un nuovo export -
        operazione che il WAF blocca spesso.

        Se le date non vengono indicate si assume il mese precedente completo:
        servono solo come etichetta del periodo nei sensori diagnostici, non
        influenzano i dati, che arrivano interamente dal file. NON tocca i
        cursori dell'import automatico (fase manuale).
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
            default_da, default_a = mese_precedente_completo(dt_util.now().date())
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
            CONF_PENDING_IS_BACKFILL: FASE_MANUALE,
        }
        self.hass.config_entries.async_update_entry(self._entry, data=nuovi_dati)

        self.pending_since = dt_util.utcnow()
        self.pending_ticket = ticket
        self._background_task = self.hass.async_create_background_task(
            self._poll_and_import(ticket, data_da, data_a, fase=FASE_MANUALE),
            name=f"{DOMAIN}_poll_import_forzato_{ticket}",
        )

    async def async_recupera_storico(self, data_da: date, data_a: date) -> None:
        """Avvia manualmente il recupero di un periodo storico.

        Il recupero dello storico non è automatico: le API accettano al
        massimo 6 mesi per richiesta e ogni richiesta può restare in coda
        per ore, quindi è l'utente a decidere quando e quanto recuperare.
        Vale per l'intera configurazione (tutti i POD) e NON tocca i cursori
        dell'import automatico: è un percorso indipendente.

        Solleva ServiceValidationError se il periodo non è valido: meglio un
        errore immediato e comprensibile in interfaccia che una richiesta che
        il distributore rifiuterebbe ore dopo.
        """
        if data_da > data_a:
            raise ServiceValidationError(
                f"La data di inizio ({data_da}) è successiva a quella di fine ({data_a})."
            )

        oggi = dt_util.now().date()
        ultimo_utile = ultimo_giorno_mese(ultimo_mese_chiuso(oggi))
        if data_a > ultimo_utile:
            raise ServiceValidationError(
                f"La data di fine ({data_a}) è troppo recente: le CURVE si fermano "
                f"al mese solare concluso, quindi al massimo fino al {ultimo_utile}."
            )

        piu_vecchia_ammessa = date(oggi.year - MAX_ANNI_STORICO, oggi.month, 1)
        if data_da < piu_vecchia_ammessa:
            raise ServiceValidationError(
                f"La data di inizio ({data_da}) è troppo lontana: le CURVE sono "
                f"disponibili solo per gli ultimi {MAX_ANNI_STORICO} anni "
                f"(non prima del {piu_vecchia_ammessa})."
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
        fasi non più esistenti (es. il vecchio "giornaliero"): in dubbio si
        ricade su FASE_AUTOMATICA.
        """
        valore = self._entry.data.get(CONF_PENDING_IS_BACKFILL)
        if isinstance(valore, str) and valore in (FASE_AUTOMATICA, FASE_STORICO, FASE_MANUALE):
            return valore
        return FASE_AUTOMATICA

    def _prossima_richiesta(self) -> tuple[str | None, date, date]:
        """Decide quale mese chiedere in questo ciclo.

        Guarda i cursori di tutti i POD: se almeno uno punta a un mese già
        CHIUSO, sceglie il più vecchio tra quelli arretrati e restituisce il
        suo primo/ultimo giorno. Se nessun cursore è arretrato (tutti fermi
        al mese corrente, in attesa che si chiuda) non c'è nulla da chiedere.

        Restituisce (fase, data_da, data_a); fase è None se non c'è nulla da
        chiedere adesso.
        """
        oggi = dt_util.now().date()
        cursori = self._cursori()
        arretrati = [m for m in cursori.values() if mese_e_chiuso(m, oggi)]
        if not arretrati:
            _LOGGER.debug(
                "Nessun cursore punta a un mese chiuso (cursori: %s): niente da chiedere",
                cursori,
            )
            return None, oggi, oggi

        mese = min(arretrati)
        return FASE_AUTOMATICA, primo_giorno_mese(mese), ultimo_giorno_mese(mese)

    async def _async_update_data(self) -> dict:
        self._pulisci_chiavi_obsolete()

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
            # bloccato dal WAF proprio mentre il distributore sta già
            # lavorando sul ticket precedente).
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
                self.data or {"stato": "nessun mese chiuso da importare"}
            )

        mese = mese_str(data_da)

        # Gruppo = i POD il cui cursore è proprio questo mese. Una sola
        # requestExport li copre tutti (il file contiene tutti i POD del
        # gruppo): c'è un solo slot per il ticket pendente, e ogni
        # requestResult può restare in coda per ore.
        cursori = self._cursori()
        gruppo = [p for p in self._pods if cursori.get(p["pod"]) == mese]
        codici_gruppo = [p["pod"] for p in gruppo]

        if await self._periodo_gia_coperto(data_a, codici_gruppo):
            _LOGGER.debug(
                "Mese %s già coperto dalle statistiche esistenti per %s: avanzo i cursori",
                mese,
                ", ".join(codici_gruppo),
            )
            for pod in codici_gruppo:
                self._avanza_cursore(pod)
            return await self._con_ultime_date(
                self.data or {"stato": f"mese {mese} già coperto dai dati esistenti"}
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
            ticket = await self.api.request_export(data_da, data_a, gruppo, mode=MODE_CURVE)
        except PcfAuthError as err:
            # Solleva ConfigEntryAuthFailed: HA lo gestisce da solo avviando
            # automaticamente il flusso di reauth con async_step_reauth.
            raise ConfigEntryAuthFailed(f"Credenziali non valide: {err}") from err
        except PcfApiError as err:
            # Nessuna coda da alimentare: i cursori del gruppo restano dove
            # sono e il mese viene richiesto di nuovo al ciclo successivo. Se
            # il problema è transitorio si recupera da solo; se è permanente
            # (mese mai pubblicato) il cursore resta fermo lì e l'utente ha
            # comunque contatore_letture.recupera_storico.
            raise UpdateFailed(f"Errore chiamando requestExport: {err}") from err

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
            name=f"{DOMAIN}_poll_import_{fase}_{mese}",
        )

        return await self._con_ultime_date(
            {
                "stato": "richiesta inviata, in attesa del file",
                "ticket": ticket,
                "periodo": f"{data_da.isoformat()} - {data_a.isoformat()}",
                "fase": fase,
            }
        )

    async def _periodo_gia_coperto(self, data_a: date, pods: list[str]) -> bool:
        """True se TUTTI i POD indicati hanno già dati persistenti (nelle
        external statistics) che coprono almeno fino a 'data_a'.

        Usa lo stesso stato che legge il sensore 'Ultima data disponibile':
        se i dati di un mese esistono già ma il cursore era rimasto indietro
        (cursore perso, import fatto a mano), il ciclo lo fa avanzare senza
        rifare la richiesta.
        """
        if not pods:
            return False
        for pod in pods:
            data_disp = await async_get_ultima_data_disponibile(self.hass, pod)
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
        cursori = self._entry.data.get(CONF_MESE_DA_IMPORTARE) or {}
        for pod_conf in self._pods:
            pod = pod_conf["pod"]
            data_disp = await async_get_ultima_data_disponibile(self.hass, pod)
            if data_disp is not None:
                ultime_date[pod] = data_disp.isoformat()
        return {**dati, "ultime_date_per_pod": ultime_date, "mese_da_importare_per_pod": dict(cursori)}

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
            # Unico caso in cui ha senso scartare il ticket: il distributore
            # dice esplicitamente che non è collegato a nessun dato.
            _LOGGER.error("Ticket %s non valido lato %s: %s", ticket, self._display_name, err)
            self.pending_since = None
            self.pending_ticket = None
            self._pulisci_ticket_pendente()
            # I cursori del gruppo NON vengono avanzati: il mese verrà
            # richiesto di nuovo al prossimo ciclo (nessuna coda).
            dati = await self._con_ultime_date(
                {**(self.data or {}), "stato": f"ticket non valido: {err}", "ultimo_errore": str(err)}
            )
            self.async_set_updated_data(dati)
            return
        except PcfApiError as err:
            # Timeout del polling, blocco WAF, errore di rete: il ticket lato
            # distributore resta valido, quindi lo CONSERVIAMO e al prossimo
            # ciclo riprendiamo da lì invece di richiederne uno nuovo.
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
            _LOGGER.debug(
                "File ricevuto per il ticket %s (%d byte), avvio parsing", ticket, len(zip_bytes)
            )
            # unzip + decode + parsing CSV di un file anche multi-mese: sincrono
            # e potenzialmente pesante, quindi fuori dall'event loop.
            risultati = await self.hass.async_add_executor_job(parse_curve_zip, zip_bytes)
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

            # Solo il ciclo automatico tocca i cursori: ogni POD del gruppo
            # per cui il file contiene dati avanza al mese successivo; gli
            # altri restano dove sono e vengono riprovati al ciclo dopo.
            if fase == FASE_AUTOMATICA:
                mese = mese_str(data_da)
                cursori = self._entry.data.get(CONF_MESE_DA_IMPORTARE) or {}
                for pod_conf in self._pods:
                    pod = pod_conf["pod"]
                    if cursori.get(pod) != mese:
                        continue
                    if risultati.get(pod) and risultati[pod].punti:
                        self._avanza_cursore(pod)
                    else:
                        _LOGGER.info(
                            "POD %s: il file per %s non contiene dati, cursore fermo, "
                            "riprovo al prossimo ciclo",
                            pod,
                            mese,
                        )
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception(
                "Errore elaborando il file ricevuto per il ticket %s: %s. Il ticket viene "
                "CONSERVATO: il file lato distributore è valido, il problema è "
                "nell'elaborazione locale, quindi al prossimo ciclo verrà riscaricato con lo "
                "stesso ticket invece di sprecarne uno nuovo (che il WAF potrebbe bloccare).",
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
