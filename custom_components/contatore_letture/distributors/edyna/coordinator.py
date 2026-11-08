"""DataUpdateCoordinator per Edyna.

Design (il "perché" in documentation/protocols/edyna-protocol.md):

  - Il portale mostra un mese per volta (una riga per giorno), dopo un
    login e 4-6 postback. Si chiede quindi un mese alla volta, e il ciclo
    tocca il portale al massimo una volta al giorno, dopo un orario scelto a
    caso per installazione (salvato nelle opzioni, come gli altri
    distributori). Il primo ciclo dopo la configurazione parte subito, per
    verificare login e POD.

  - Nessun "giorno atteso" con coda di retry come SET/Ireti: nella cattura
    del 05/11/2026 l'ultimo giorno disponibile era il 25/10 (11 giorni di
    ritardo, poi recuperati: dal 07/11 si vede il giorno prima). Ogni richiesta rilegge il
    mese dell'ultimo dato importato (meno GIORNI_DI_RIPASSO) fino a quello in
    corso e importa tutto ciò che c'è di nuovo: l'import è idempotente
    (statistics_common fonde e ricalcola le somme), quindi riprendere un
    giorno già importato non costa nulla, e un buco nel mese si chiude da
    solo appena il portale lo riempie.

  - Primo avvio: gli ultimi MESI_PRIMO_AVVIO mesi. Se si è rimasti indietro di
    più di MAX_MESI_PER_CICLO mesi si recupera a tranche, un ciclo orario
    dopo l'altro; lo storico più vecchio con contatore_letture.recupera_storico.

  - Login rifatto a ogni richiesta (cookie di sessione del portale, nessun
    token da conservare). Credenziali non valide sollevano
    ConfigEntryAuthFailed (HA avvia il reauth).
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
from .api import EdynaApiClient, EdynaApiError
from .auth import (
    EdynaAuthClient,
    EdynaAuthError,
    EdynaInvalidCredentials,
    nuovo_cookie_jar,
)
from .const import (
    CONF_DATA_INSTALLAZIONE,
    CONF_ORARIO_RICHIESTA,
    CONF_PASSWORD,
    CONF_PODS,
    CONF_USERNAME,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    GIORNI_DI_RIPASSO,
    MAX_MESI_PER_CICLO,
    MAX_MESI_RECUPERO_STORICO,
    MESI_PRIMO_AVVIO,
    orario_casuale,
    orario_da_opzioni,
)
from .statistics import async_get_ultima_data_disponibile, async_import_ore, ore_del_giorno

_LOGGER = logging.getLogger(__name__)


def mesi_nel_periodo(data_da: date, data_a: date) -> list[tuple[int, int]]:
    """[(anno, mese)] dei mesi toccati dall'intervallo, dal più vecchio."""
    mesi, (anno, mese) = [], (data_da.year, data_da.month)
    while (anno, mese) <= (data_a.year, data_a.month):
        mesi.append((anno, mese))
        anno, mese = (anno + 1, 1) if mese == 12 else (anno, mese + 1)
    return mesi


def _mese_precedente(giorno: date, quanti: int) -> date:
    """Primo giorno del mese che precede di 'quanti' mesi quello di 'giorno'."""
    indice = giorno.year * 12 + (giorno.month - 1) - quanti
    return date(indice // 12, indice % 12 + 1, 1)


class EdynaCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} (Edyna)",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
            config_entry=entry,
        )
        self.entry = entry
        self.pods: list[str] = list(entry.data[CONF_PODS])
        self._session = async_create_clientsession(hass, cookie_jar=nuovo_cookie_jar())
        self._auth = EdynaAuthClient(self._session)
        self._orario_ripiego: time | None = None
        # Giorno (locale) dell'ultima richiesta completata: una al giorno.
        self._ultima_richiesta: date | None = None
        # Rimasti mesi da leggere dopo il tetto per ciclo: richiedi subito.
        self._servono_altri_cicli = False
        self._assicura_orario_richiesta()

    def _assicura_orario_richiesta(self) -> None:
        """Entry senza orario: ne sceglie uno casuale e lo salva nelle
        opzioni, così resta lo stesso ai riavvii."""
        if CONF_ORARIO_RICHIESTA in self.entry.options:
            return
        orario = orario_casuale()
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, CONF_ORARIO_RICHIESTA: orario}
        )
        _LOGGER.info("Orario della richiesta giornaliera scelto a caso: %s", orario)

    @property
    def _orario_richiesta(self) -> time:
        orario = orario_da_opzioni(self.entry.options)
        if orario is not None:
            return orario
        if self._orario_ripiego is None:
            self._orario_ripiego = time.fromisoformat(orario_casuale())
            _LOGGER.warning(
                "Orario richiesta non valido nelle opzioni (%r): uso le %s",
                self.entry.options.get(CONF_ORARIO_RICHIESTA),
                self._orario_ripiego.strftime("%H:%M"),
            )
        return self._orario_ripiego

    def _e_ora_di_chiedere(self) -> bool:
        """Al primo avvio subito (verifica login e POD); poi una volta al
        giorno dopo l'orario, o subito se restano mesi da recuperare."""
        if not self.entry.data.get(CONF_DATA_INSTALLAZIONE):
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CONF_DATA_INSTALLAZIONE: dt_util.now().date().isoformat(),
                },
            )
            _LOGGER.info(
                "Primo avvio: leggo subito gli ultimi %d mesi per verificare login e POD. "
                "Da domani le richieste partiranno dopo le %s; per lo storico più vecchio "
                "usa l'azione contatore_letture.recupera_storico.",
                MESI_PRIMO_AVVIO,
                self._orario_richiesta.strftime("%H:%M"),
            )
            return True
        if self._servono_altri_cicli:
            return True
        adesso = dt_util.now()
        return adesso.time() >= self._orario_richiesta and self._ultima_richiesta != adesso.date()

    async def _async_login(self) -> EdynaApiClient:
        try:
            pagina = await self._auth.async_login(
                self.entry.data[CONF_USERNAME], self.entry.data[CONF_PASSWORD]
            )
        except EdynaInvalidCredentials as err:
            raise ConfigEntryAuthFailed(f"Credenziali Edyna non valide: {err}") from err
        except EdynaAuthError as err:
            raise UpdateFailed(f"Login Edyna fallito: {err}") from err
        return EdynaApiClient(self._session, pagina)

    async def _async_giorni_mese(
        self,
        api: EdynaApiClient,
        pod: str,
        anno: int,
        mese: int,
        data_da: date,
        data_a: date,
    ) -> dict[date, dict[datetime, float]]:
        """I giorni validi del mese dentro [data_da, data_a]. EdynaApiError
        se il portale risponde male."""
        curva = await api.async_get_curva_mese(pod, anno, mese)
        giorni: dict[date, dict[datetime, float]] = {}
        for giorno, valori in sorted(curva.items()):
            if not data_da <= giorno <= data_a:
                continue
            ore = ore_del_giorno(valori, giorno)
            if ore is not None:
                giorni[giorno] = ore
        return giorni

    async def _async_mesi_da_leggere(self, pod: str) -> tuple[list[tuple[int, int]], date, date]:
        """Mesi da chiedere per un POD e la finestra di giorni da importare."""
        oggi = dt_util.now().date()
        ultima = await async_get_ultima_data_disponibile(self.hass, pod)
        if ultima is None:
            da = _mese_precedente(oggi, MESI_PRIMO_AVVIO - 1)
        else:
            da = ultima - timedelta(days=GIORNI_DI_RIPASSO)
        return mesi_nel_periodo(da, oggi), da, oggi

    async def _async_update_data(self) -> dict:
        precedenti = (self.data or {}).get("by_pod", {})
        by_pod: dict[str, dict] = {
            pod: dict(precedenti.get(pod) or {
                "ultimo_giorno_importato": None,
                "kwh_ultimo_giorno_importato": None,
            })
            for pod in self.pods
        }

        if self._e_ora_di_chiedere():
            self._servono_altri_cicli = False
            api = await self._async_login()
            for pod in self.pods:
                await self._async_ciclo_pod(api, pod, by_pod[pod])
            self._ultima_richiesta = dt_util.now().date()

        for pod, dati_pod in by_pod.items():
            ultima = await async_get_ultima_data_disponibile(self.hass, pod)
            dati_pod["ultima_data_disponibile"] = ultima.isoformat() if ultima else None
        return {"by_pod": by_pod}

    async def _async_ciclo_pod(self, api: EdynaApiClient, pod: str, dati_pod: dict) -> None:
        mesi, data_da, data_a = await self._async_mesi_da_leggere(pod)
        if len(mesi) > MAX_MESI_PER_CICLO:
            mesi = mesi[:MAX_MESI_PER_CICLO]
            self._servono_altri_cicli = True

        ricevuti: dict[date, dict[datetime, float]] = {}
        errore: EdynaApiError | None = None
        for anno, mese in mesi:
            try:
                ricevuti.update(await self._async_giorni_mese(api, pod, anno, mese, data_da, data_a))
            except EdynaApiError as err:
                errore = err
                self._servono_altri_cicli = False
                break

        # Prima di uscire si salva quello che è arrivato.
        if ricevuti:
            ore: dict[datetime, float] = {}
            for ore_giorno in ricevuti.values():
                ore.update(ore_giorno)
            await async_import_ore(self.hass, pod, ore)
            ultimo = max(ricevuti)
            dati_pod["ultimo_giorno_importato"] = ultimo.isoformat()
            dati_pod["kwh_ultimo_giorno_importato"] = sum(ricevuti[ultimo].values())
            _LOGGER.info(
                "POD %s: importati %d giorni Edyna (fino al %s)", pod, len(ricevuti), ultimo
            )
        elif errore is None:
            _LOGGER.info("POD %s: nessun giorno nuovo sul portale Edyna", pod)

        if errore is not None:
            raise UpdateFailed(f"Errore leggendo le curve Edyna del POD {pod}: {errore}") from errore

    # ------------------------------------------------------------------
    # Recupero storico manuale (azione contatore_letture.recupera_storico)
    # ------------------------------------------------------------------

    async def async_recupera_storico(
        self, data_da: date, data_a: date, pod: str | None = None
    ) -> None:
        """Recupera e importa la curva oraria per [data_da, data_a], per
        tutti i POD della entry o solo per 'pod'. Una lettura per mese.

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

        mesi = mesi_nel_periodo(data_da, data_a)
        if len(mesi) > MAX_MESI_RECUPERO_STORICO:
            raise ServiceValidationError(
                f"Intervallo di {len(mesi)} mesi troppo ampio per una singola richiesta "
                f"(limite di cortesia: {MAX_MESI_RECUPERO_STORICO} mesi, ogni mese costa "
                "diverse richieste al portale). Ripeti l'azione su periodi più corti."
            )

        api = await self._async_login()
        _LOGGER.info(
            "Recupero storico Edyna avviato: %s - %s (%d mesi, POD: %s)",
            data_da, data_a, len(mesi), ", ".join(pod_da_recuperare),
        )

        fallimenti: list[str] = []
        giorni_importati = 0

        for pod_corrente in pod_da_recuperare:
            trovati = 0
            for anno, mese in mesi:
                try:
                    ricevuti = await self._async_giorni_mese(
                        api, pod_corrente, anno, mese, data_da, data_a
                    )
                except EdynaApiError as err:
                    _LOGGER.warning(
                        "POD %s: errore recuperando %02d/%d: %s", pod_corrente, mese, anno, err
                    )
                    fallimenti.append(f"{pod_corrente}/{mese:02d}-{anno}: {err}")
                    continue
                if ricevuti:
                    ore: dict[datetime, float] = {}
                    for ore_giorno in ricevuti.values():
                        ore.update(ore_giorno)
                    await async_import_ore(self.hass, pod_corrente, ore)
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
