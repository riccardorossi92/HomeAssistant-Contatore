"""DataUpdateCoordinator for e-Distribuzione.

Handles: refreshing the access_token via the stored refresh_token before each
poll, then:

- import automatico della curva di carico giornaliera (una volta al giorno,
  dopo l'orario configurato, con coda di retry per i giorni non ancora
  disponibili - stesso meccanismo di pcf_common, vedi le note su
  RITARDO_DATI_GIORNI in const.py sul perché qui è un'ipotesi non
  confermata invece di un valore verificato);
- lettura mensile (reading + time-of-use) per il POD configurato, come già
  prima.

async_recupera_storico permette di recuperare un intervallo di date a
mano (azione contatore_letture.recupera_storico, condivisa con
Duereti/Unareti): a differenza loro, qui non esiste un endpoint "per
intervallo" a grana oraria, quindi il recupero è sequenziale, un giorno
alla volta.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EdistribuzioneApiClient, EdistribuzioneApiError
from .auth import EdistribuzioneAuthClient, EdistribuzioneAuthError
from .const import (
    CONF_DATA_INSTALLAZIONE,
    CONF_GIORNI_DA_RIPROVARE,
    CONF_ORA_RICHIESTA,
    CONF_POD,
    CONF_REFRESH_TOKEN,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    MAX_GIORNI_IN_CODA,
    MAX_GIORNI_RECUPERO_STORICO,
    MAX_TENTATIVI_PER_GIORNO,
    ORA_MINIMA_RICHIESTA,
    RITARDO_DATI_GIORNI,
)
from .statistics import async_get_ultima_data_disponibile, async_import_curva_giornaliera
from ...const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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
        )
        self.entry = entry
        self.pod: str = entry.data[CONF_POD]
        session = async_get_clientsession(hass)
        self._auth = EdistribuzioneAuthClient(session)
        self._api = EdistribuzioneApiClient(session, access_token="")

    async def _async_ensure_token(self) -> None:
        refresh_token = self.entry.data[CONF_REFRESH_TOKEN]
        try:
            tokens = await self._auth.async_refresh_access_token(refresh_token)
        except EdistribuzioneAuthError as err:
            # A failed refresh almost certainly means the refresh_token was
            # revoked (password change, Enel-side session cleanup, ...) and
            # the user needs to go through config_flow's re-auth again.
            raise UpdateFailed(f"Token refresh failed: {err}") from err

        self._api.update_token(tokens.access_token)

        if tokens.refresh_token != refresh_token:
            new_data = dict(self.entry.data)
            new_data[CONF_REFRESH_TOKEN] = tokens.refresh_token
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    # ------------------------------------------------------------------
    # Orario configurabile + coda dei giorni da riprovare
    # (stesso meccanismo di pcf_common/coordinator.py, duplicato invece di
    # condiviso: vedi la nota in const.py su RITARDO_DATI_GIORNI)
    # ------------------------------------------------------------------

    @property
    def _ora_richiesta(self) -> int:
        """Ora (locale) a partire dalla quale chiedere la curva del giorno
        prima. Configurabile dalle opzioni: non sappiamo ancora quando
        E-Distribuzione pubblica i dati (vedi nota in const.py), quindi se
        capitano richieste spesso vuote conviene spostarla più avanti."""
        valore = self.entry.options.get(CONF_ORA_RICHIESTA)
        if valore is None:
            return ORA_MINIMA_RICHIESTA
        try:
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
        """Coda dei giorni da riprovare, {data ISO: tentativi fatti}."""
        return dict(self.entry.data.get(CONF_GIORNI_DA_RIPROVARE) or {})

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
                "Giorni abbandonati dopo %d tentativi senza dati da E-Distribuzione: %s. "
                "Se servono, richiedili con l'azione contatore_letture.recupera_storico.",
                MAX_TENTATIVI_PER_GIORNO,
                ", ".join(sorted(abbandonati)),
            )

        if len(pulita) > MAX_GIORNI_IN_CODA:
            tenuti = sorted(pulita, reverse=True)[:MAX_GIORNI_IN_CODA]
            scartati = set(pulita) - set(tenuti)
            _LOGGER.warning(
                "Coda dei giorni da riprovare oltre %d elementi: scarto i più vecchi (%s)",
                MAX_GIORNI_IN_CODA,
                ", ".join(sorted(scartati)),
            )
            pulita = {g: pulita[g] for g in tenuti}

        if pulita != self.entry.data.get(CONF_GIORNI_DA_RIPROVARE):
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_GIORNI_DA_RIPROVARE: pulita},
            )

    def _accoda_giorno(self, giorno: date) -> None:
        coda = self._leggi_coda()
        chiave = giorno.isoformat()
        coda[chiave] = coda.get(chiave, 0) + 1
        _LOGGER.info(
            "Giorno %s senza dati da E-Distribuzione: messo in coda per riprovare "
            "(tentativo %d di %d)",
            chiave,
            coda[chiave],
            MAX_TENTATIVI_PER_GIORNO,
        )
        self._scrivi_coda(coda)

    def _rimuovi_dalla_coda(self, giorni: list[date]) -> None:
        coda = self._leggi_coda()
        rimossi = [g.isoformat() for g in giorni if g.isoformat() in coda]
        if not rimossi:
            return
        for chiave in rimossi:
            del coda[chiave]
        _LOGGER.info("Dati ricevuti per %s: rimossi dalla coda", ", ".join(rimossi))
        self._scrivi_coda(coda)

    async def _prossima_richiesta(self) -> date | None:
        """Decide se c'è un giorno da chiedere in questo ciclo.

        Stessa logica di pcf_common: al primo avvio chiede subito il
        giorno atteso (per verificare da subito che POD/token siano
        validi, senza aspettare l'orario configurato); nei cicli
        successivi aspetta l'orario configurato, poi smaltisce prima la
        coda dei giorni arretrati (dal più vecchio) e infine il giorno
        atteso - a meno che non risulti già coperto dalle statistiche
        esistenti.
        """
        oggi = date.today()
        installazione = self.entry.data.get(CONF_DATA_INSTALLAZIONE)
        atteso = oggi - timedelta(days=RITARDO_DATI_GIORNI)

        if not installazione:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_DATA_INSTALLAZIONE: oggi.isoformat()},
            )
            _LOGGER.info(
                "Primo avvio: richiedo la curva del %s per verificare POD e token. "
                "Da domani le richieste partiranno dopo le %d:00 (modificabile dalle "
                "opzioni); per lo storico usa l'azione contatore_letture.recupera_storico.",
                atteso,
                self._ora_richiesta,
            )
            return atteso

        adesso = dt_util.now()
        if adesso.hour < self._ora_richiesta:
            _LOGGER.debug(
                "Sono le %02d:%02d, attendo le %d:00 prima di chiedere la curva",
                adesso.hour,
                adesso.minute,
                self._ora_richiesta,
            )
            return None

        coda = self._leggi_coda()
        arretrati = sorted(g for g in coda if date.fromisoformat(g) < atteso)
        if arretrati:
            giorno = date.fromisoformat(arretrati[0])
            _LOGGER.debug(
                "Riprovo il giorno arretrato %s (%d ancora in coda)", giorno, len(arretrati)
            )
            return giorno

        ultima_disponibile = await async_get_ultima_data_disponibile(self.hass, self.pod)
        if ultima_disponibile and ultima_disponibile >= atteso:
            _LOGGER.debug(
                "Giorno %s già coperto dalle statistiche esistenti (ultima: %s)",
                atteso,
                ultima_disponibile,
            )
            return None

        return atteso

    # ------------------------------------------------------------------
    # Ciclo di polling automatico
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        await self._async_ensure_token()

        giorno_richiesto = await self._prossima_richiesta()
        if giorno_richiesto is not None:
            try:
                curva = await self._api.async_get_daily_load_profile(self.pod, giorno_richiesto)
            except EdistribuzioneApiError as err:
                raise UpdateFailed(
                    f"Errore chiamando async_get_daily_load_profile: {err}"
                ) from err

            if _curva_ha_dati(curva):
                await async_import_curva_giornaliera(self.hass, self.pod, curva)
                self._rimuovi_dalla_coda([giorno_richiesto])
            else:
                self._accoda_giorno(giorno_richiesto)

        today = date.today()
        month_start = today.replace(day=1)
        six_months_ago = (month_start - timedelta(days=1)).replace(day=1)
        # Cheap approximation of "6 months back" without extra deps; good
        # enough for a rolling time-of-use window.
        for _ in range(4):
            six_months_ago = (six_months_ago - timedelta(days=1)).replace(day=1)

        try:
            reading = await self._api.async_get_reading(
                self.pod, today - timedelta(days=45), today
            )
            time_of_use = await self._api.async_get_monthly_time_of_use(
                self.pod, six_months_ago, today
            )
        except EdistribuzioneApiError as err:
            raise UpdateFailed(f"Error fetching e-Distribuzione data: {err}") from err

        return {
            "reading": reading,
            "time_of_use": time_of_use,
            "ultimo_giorno_curva_richiesto": (
                giorno_richiesto.isoformat() if giorno_richiesto else None
            ),
        }

    # ------------------------------------------------------------------
    # Recupero storico manuale (azione contatore_letture.recupera_storico)
    # ------------------------------------------------------------------

    async def async_recupera_storico(self, data_da: date, data_a: date) -> None:
        """Recupera e importa la curva di carico giornaliera per ogni
        giorno nell'intervallo [data_da, data_a].

        A differenza di pcf_common (che fa un'unica richiesta per l'intero
        periodo tramite requestExport), l'API E-Distribuzione accetta un
        solo giorno per chiamata: il recupero è quindi necessariamente
        sequenziale. Giorni senza dati non fanno fallire l'intera
        operazione: vengono segnalati alla fine con un riepilogo nei log,
        e restano comunque disponibili per un retry successivo tramite
        la stessa azione.
        """
        if data_da > data_a:
            raise ServiceValidationError(
                f"La data di inizio ({data_da}) è successiva a quella di fine ({data_a})."
            )

        ultimo_utile = date.today() - timedelta(days=RITARDO_DATI_GIORNI)
        if data_a > ultimo_utile:
            raise ServiceValidationError(
                f"La data di fine ({data_a}) è troppo recente: al momento si assume che i "
                f"dati siano disponibili con almeno un giorno di ritardo (non confermato "
                f"con certezza per E-Distribuzione), quindi al massimo fino al {ultimo_utile}."
            )

        giorni_totali = (data_a - data_da).days + 1
        if giorni_totali > MAX_GIORNI_RECUPERO_STORICO:
            raise ServiceValidationError(
                f"Intervallo di {giorni_totali} giorni troppo ampio per una singola "
                f"richiesta (limite di cortesia: {MAX_GIORNI_RECUPERO_STORICO} giorni, "
                "~6 mesi - non è un vincolo noto delle API E-Distribuzione, solo una "
                "cautela). Ripeti l'azione su periodi più corti."
            )

        await self._async_ensure_token()

        _LOGGER.info("Recupero storico E-Distribuzione avviato: %s - %s", data_da, data_a)

        giorno = data_da
        importati = 0
        senza_dati: list[date] = []
        while giorno <= data_a:
            try:
                curva = await self._api.async_get_daily_load_profile(self.pod, giorno)
            except EdistribuzioneApiError as err:
                _LOGGER.warning("Errore recuperando il giorno %s: %s", giorno, err)
                senza_dati.append(giorno)
                giorno += timedelta(days=1)
                continue

            if _curva_ha_dati(curva):
                await async_import_curva_giornaliera(self.hass, self.pod, curva)
                importati += 1
                self._rimuovi_dalla_coda([giorno])
            else:
                senza_dati.append(giorno)

            giorno += timedelta(days=1)

        dettaglio_vuoti = ""
        if senza_dati:
            elenco = ", ".join(g.isoformat() for g in senza_dati[:10])
            if len(senza_dati) > 10:
                elenco += f", e altri {len(senza_dati) - 10}"
            dettaglio_vuoti = f" ({elenco})"

        _LOGGER.info(
            "Recupero storico E-Distribuzione %s - %s completato: %d giorni importati, "
            "%d senza dati%s",
            data_da,
            data_a,
            importati,
            len(senza_dati),
            dettaglio_vuoti,
        )

    @property
    def api(self) -> EdistribuzioneApiClient:
        """Expose the API client for on-demand calls."""
        return self._api
