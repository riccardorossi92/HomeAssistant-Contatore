"""DataUpdateCoordinator per Areti.

Design completo (e il "perché") in documentation/areti-protocol.md,
sezione "Design del coordinator". In sintesi: Areti pubblica i dati a
MESE SOLARE CHIUSO (non giorno per giorno come i PCF/E-Distribuzione), e
non è noto un ritardo fisso da cui dedurre quale mese chiedere ogni
volta - quindi il coordinator usa un CURSORE PERSISTITO per POD
('mese_da_importare' su entry.data), non il pattern "sempre il mese/
giorno precedente rispetto a oggi" degli altri distributori:

  - ogni ciclo (una volta al giorno - la granularità è mensile, controllare
    più spesso non avrebbe senso), per ciascun POD chiede getMisurazioni
    per il suo cursore;
  - vuoto -> non fa nulla, resta fermo lì, riprova al ciclo successivo.
    NESSUN abbandono automatico (scelta deliberata, non un TODO
    dimenticato): un mese mai pubblicato blocca il cursore di quel POD
    finché non arriva. Chi vuole sbloccarsi nel frattempo ha comunque
    l'azione condivisa contatore_letture.recupera_storico;
  - disponibile -> importa (statistics.py) e avanza il cursore al mese
    successivo. Quando raggiunge il mese in corso lo riprova come
    qualunque altro mese, nessun caso speciale.
  - primo avvio (o POD aggiunto in seguito): nessun backfill, il cursore
    parte dal mese corrente.

Niente refresh_token (a differenza di E-Distribuzione): ogni ciclo rifà
login da zero (auth.py) - a questa cadenza (una volta al giorno) il
costo di qualche richiesta in più è irrilevante, ed evita di dover
gestire la scadenza della sessione a metà catena (non ancora osservata,
vedi "Cosa resta aperto" in areti-protocol.md).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ...const import DOMAIN
from .api import AretiApiClient, AretiApiError
from .auth import AretiAuraContext, AretiAuthClient, AretiAuthError, build_ssl_context
from .const import (
    COMPONENTE_ENERGIA_DEFAULT,
    CONF_EMAIL,
    CONF_MESE_DA_IMPORTARE,
    CONF_PASSWORD,
    CONF_PODS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    MAX_MESI_RECUPERO_STORICO,
)
from .statistics import async_get_ultima_data_disponibile, async_import_curva_mensile

_LOGGER = logging.getLogger(__name__)


def _mese_anno_di(giorno: date) -> str:
    """date -> 'MMYYYY', il formato richiesto da getMisurazioni."""
    return f"{giorno.month:02d}{giorno.year}"


def _mese_successivo(mese_anno: str) -> str:
    """'MMYYYY' -> 'MMYYYY' del mese successivo."""
    mese, anno = int(mese_anno[:2]), int(mese_anno[2:])
    if mese == 12:
        return f"01{anno + 1}"
    return f"{mese + 1:02d}{anno}"


def _mesi_nel_periodo(data_da: date, data_a: date) -> list[str]:
    """'MMYYYY' di ogni mese attraversato dall'intervallo [data_da, data_a]
    (estremi inclusi), in ordine, senza duplicati. Areti non offre
    granularità più fine di un mese intero (vedi async_get_misurazioni):
    chiedere un solo giorno in mezzo al mese importa comunque tutto il
    mese che lo contiene."""
    mesi: list[str] = []
    cursore = data_da.replace(day=1)
    ultimo = data_a.replace(day=1)
    while cursore <= ultimo:
        mesi.append(_mese_anno_di(cursore))
        anno, mese = cursore.year, cursore.month
        cursore = date(anno + 1, 1, 1) if mese == 12 else date(anno, mese + 1, 1)
    return mesi


class AretiCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} (Areti)",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
            config_entry=entry,
        )
        self.entry = entry
        self.pods: list[str] = list(entry.data[CONF_PODS])
        # Sessione DEDICATA (non quella condivisa di Home Assistant):
        # serve un contesto SSL con l'intermedio DigiCert aggiunto (vedi
        # auth.build_ssl_context) e una jar di cookie che non si mescoli
        # con quella di altre integrazioni.
        self._session = async_create_clientsession(
            hass, connector=aiohttp.TCPConnector(ssl=build_ssl_context())
        )
        self._auth = AretiAuthClient(self._session)
        # {pod: (codiceBP, codiceFiscale)}, risolti la prima volta che
        # servono e tenuti in memoria per la vita del coordinator (sono
        # identificativi stabili di un POD, non cambiano da un ciclo
        # all'altro) - persi a un riavvio di Home Assistant, ma
        # ririsolti senza problemi al primo ciclo successivo.
        self._config_pod: dict[str, tuple[str, str]] = {}

    async def _async_login(self) -> AretiApiClient:
        try:
            contesto: AretiAuraContext = await self._auth.async_login(
                self.entry.data[CONF_EMAIL], self.entry.data[CONF_PASSWORD]
            )
        except AretiAuthError as err:
            raise UpdateFailed(f"Login Areti fallito: {err}") from err
        return AretiApiClient(self._session, contesto)

    async def _async_config_pod(self, api: AretiApiClient, pod: str) -> tuple[str, str]:
        """codiceBP/codiceFiscale per un POD, risolti una volta sola e
        tenuti in cache (vedi self._config_pod)."""
        if pod not in self._config_pod:
            config = await api.async_get_configurations(pod)
            self._config_pod[pod] = (config["codiceBP"], config["codiceFiscale"])
        return self._config_pod[pod]

    # ------------------------------------------------------------------
    # Cursore mensile persistito, per POD
    # ------------------------------------------------------------------

    def _leggi_cursori(self) -> dict[str, str]:
        return dict(self.entry.data.get(CONF_MESE_DA_IMPORTARE) or {})

    def _scrivi_cursore(self, pod: str, mese_anno: str) -> None:
        cursori = self._leggi_cursori()
        if cursori.get(pod) == mese_anno:
            return
        cursori[pod] = mese_anno
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_MESE_DA_IMPORTARE: cursori},
        )

    def _cursore_pod(self, pod: str) -> str:
        """Mese da importare per un POD: se non ancora inizializzato
        (primo avvio, o POD aggiunto in seguito tramite le opzioni),
        parte dal mese corrente - nessun backfill storico automatico
        (scelta deliberata, vedi il docstring del modulo)."""
        cursori = self._leggi_cursori()
        mese = cursori.get(pod)
        if mese is None:
            mese = _mese_anno_di(dt_util.now().date())
            self._scrivi_cursore(pod, mese)
        return mese

    # ------------------------------------------------------------------
    # Ciclo di polling automatico
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        api = await self._async_login()

        by_pod: dict[str, dict] = {}
        for pod in self.pods:
            mese_richiesto = self._cursore_pod(pod)
            kwh_ultimo_mese_importato = None
            mese_importato = None
            try:
                codice_bp, codice_fiscale = await self._async_config_pod(api, pod)
                dettaglio = await api.async_get_misurazioni(
                    pod, codice_bp, codice_fiscale, mese_richiesto, COMPONENTE_ENERGIA_DEFAULT
                )
            except AretiApiError as err:
                raise UpdateFailed(
                    f"Errore recuperando le misure Areti per il POD {pod}, mese "
                    f"{mese_richiesto}: {err}"
                ) from err

            if dettaglio is not None:
                curva = dettaglio.get("elementiCurve") or []
                await async_import_curva_mensile(self.hass, pod, curva)
                kwh_ultimo_mese_importato = sum(
                    float(c["Value"]) for c in curva if c.get("Value") not in (None, "")
                )
                mese_importato = mese_richiesto
                self._scrivi_cursore(pod, _mese_successivo(mese_richiesto))

            ultima_data_disponibile = await async_get_ultima_data_disponibile(self.hass, pod)
            by_pod[pod] = {
                "mese_da_importare": self._cursore_pod(pod),
                "ultimo_mese_importato": mese_importato,
                "kwh_ultimo_mese_importato": kwh_ultimo_mese_importato,
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
        """Recupera e importa i mesi attraversati da [data_da, data_a].

        Se 'pod' è omesso, lo fa per TUTTI i POD configurati sulla entry;
        se specificato, solo per quello. A differenza di PCF/
        E-Distribuzione, Areti non ha una vera API a intervallo di date:
        'data_da'/'data_a' restano l'interfaccia del servizio (stessa UI
        degli altri distributori), ma internamente vengono convertite
        nell'insieme di mesi solari che l'intervallo attraversa, e per
        ciascuno si importa il MESE INTERO (non i soli giorni richiesti -
        l'API non offre altro). Non avanza né tocca il cursore
        dell'import automatico: sono percorsi indipendenti.
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

        mesi = _mesi_nel_periodo(data_da, data_a)
        if len(mesi) > MAX_MESI_RECUPERO_STORICO:
            raise ServiceValidationError(
                f"Intervallo di {len(mesi)} mesi troppo ampio per una singola "
                f"richiesta (limite di cortesia: {MAX_MESI_RECUPERO_STORICO} mesi). "
                "Ripeti l'azione su periodi più corti."
            )

        api = await self._async_login()

        _LOGGER.info(
            "Recupero storico Areti avviato: %s - %s (%d mesi, POD: %s)",
            data_da,
            data_a,
            len(mesi),
            ", ".join(pod_da_recuperare),
        )

        for pod_corrente in pod_da_recuperare:
            try:
                codice_bp, codice_fiscale = await self._async_config_pod(api, pod_corrente)
            except AretiApiError as err:
                _LOGGER.warning("POD %s: impossibile risolvere codiceBP/codiceFiscale: %s", pod_corrente, err)
                continue

            trovati, mancanti = [], []
            for mese_anno in mesi:
                try:
                    dettaglio = await api.async_get_misurazioni(
                        pod_corrente, codice_bp, codice_fiscale, mese_anno, COMPONENTE_ENERGIA_DEFAULT
                    )
                except AretiApiError as err:
                    _LOGGER.warning(
                        "POD %s: errore recuperando il mese %s: %s", pod_corrente, mese_anno, err
                    )
                    mancanti.append(mese_anno)
                    continue

                if dettaglio is None:
                    mancanti.append(mese_anno)
                    continue

                curva = dettaglio.get("elementiCurve") or []
                await async_import_curva_mensile(self.hass, pod_corrente, curva)
                trovati.append(mese_anno)

            _LOGGER.info(
                "POD %s: recupero storico completato, %d/%d mesi trovati%s",
                pod_corrente,
                len(trovati),
                len(mesi),
                f" (mancanti: {', '.join(mancanti)})" if mancanti else "",
            )
