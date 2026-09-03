"""Config flow di contatore_letture.

Flusso:
  1. user / provincia / comune  -> cascata regione-provincia-comune (ISTAT)
  2. lookup ARERA live sul comune scelto -> individua il distributore
  3. choose_distributor / manual_select -> fallback se ARERA da' piu'
     risultati, fallisce, o restituisce un operatore non supportato
  4. distributor_info -> mostra cosa serve, in base al distributore
  5a. (kind="pcf", Duereti/Unareti) pcf_credentials -> pcf_add_pod (loop)
  5b. (kind="edistribuzione") edistribuzione_credentials

Il flow e' UNA SOLA classe (nessuna multi-inheritance tra distributori):
gli step del ramo "pcf" sono generici e dispatchano a runtime sul modulo
giusto (self._distributor_key) leggendolo da DISTRIBUTOR_REGISTRY - stesso
principio del "source plugin" di waste_collection_schedule, adattato ai
nostri 3 distributori invece che a centinaia.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig
from homeassistant.util import dt as dt_util

from .arera_lookup import AreraLookupError, async_query_distributore
from .const import (
    CONF_CLIENT_ID,
    CONF_PODS,
    CONF_SECRET_ID,
    DOMAIN,
)
from .distributors import DISTRIBUTOR_REGISTRY, PIVA_TO_KEY
from .distributors.pcf_common.config_flow_helpers import pod_gia_configurato
from .distributors.pcf_common.const import (
    CONF_ORA_RICHIESTA,
    CONF_PENDING_DATA_A,
    CONF_PENDING_DATA_DA,
    CONF_PENDING_IS_BACKFILL,
    CONF_PENDING_TICKET,
    FASE_GIORNALIERO,
    ORA_MINIMA_RICHIESTA,
    RITARDO_VERIFICA_POD_GIORNI,
)
from .istat_comuni import async_get_comuni_tree

_LOGGER = logging.getLogger(__name__)

STEP_POD_SCHEMA = vol.Schema(
    {
        vol.Required("pod"): str,
        vol.Required("df"): str,
    }
)


def _etichetta_pod(pod_info: dict) -> str:
    """'IT001E10684497 - Via Venino 29, Moneglia (GE)' invece del solo
    codice POD nel selettore, cosi' si riconosce a colpo d'occhio quale
    immobile e' senza dover controllare altrove. Usata sia nel wizard
    iniziale (ContatoreLettureConfigFlow) sia nelle opzioni
    (ContatoreLettureOptionsFlow), stesso identico contesto in entrambe."""
    indirizzo = (
        f"{pod_info.get('PointOfMeasureStreetPrefix', '')} "
        f"{pod_info.get('PointOfMeasureStreet', '')} "
        f"{pod_info.get('PointOfMeasureStreetNumber', '')}, "
        f"{pod_info.get('PointOfMeasureMunicipality', '')} "
        f"({pod_info.get('PointOfMeasureProvince', '')})"
    ).strip()
    return f"{pod_info['IdPod']} - {indirizzo}"


class ContatoreLettureConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow per contatore_letture."""

    VERSION = 1

    def __init__(self) -> None:
        # Stato del wizard ARERA
        self._tree: dict = {}
        self._regione: str | None = None
        self._provincia: str | None = None
        self._comune_name: str | None = None
        self._candidates: list[str] = []
        self._distributor_key: str | None = None
        # Stato del ramo "pcf" (Duereti/Unareti)
        self._client_id: str | None = None
        self._secret_id: str | None = None
        self._pods: list[dict] = []
        self._ticket_verifica: str | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None
        # Stato del ramo E-Distribuzione
        self._edistribuzione_auth = None
        self._edistribuzione_session = None
        self._edistribuzione_access_token: str | None = None
        self._edistribuzione_refresh_token: str | None = None
        self._edistribuzione_pods: list[dict] = []

    # ------------------------------------------------------------------
    # Wizard ARERA: regione -> provincia -> comune -> lookup
    # ------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if not self._tree:
            self._tree = await async_get_comuni_tree(self.hass)

        if user_input is not None:
            self._regione = user_input["regione"]
            return await self.async_step_provincia()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("regione"): SelectSelector(
                    SelectSelectorConfig(options=sorted(self._tree.keys()))
                )
            }),
        )

    async def async_step_provincia(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._provincia = user_input["provincia"]
            return await self.async_step_comune()

        province = sorted(self._tree[self._regione].keys())
        return self.async_show_form(
            step_id="provincia",
            data_schema=vol.Schema({
                vol.Required("provincia"): SelectSelector(
                    SelectSelectorConfig(options=province)
                )
            }),
        )

    async def async_step_comune(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            comune_name = user_input["comune"]
            self._comune_name = comune_name
            codes = self._tree[self._regione][self._provincia][comune_name]

            try:
                operatori = await async_query_distributore(
                    self.hass,
                    codes["codice_regione"],
                    codes["codice_provincia"],
                    codes["codice_comune"],
                )
            except AreraLookupError as exc:
                _LOGGER.warning("Lookup ARERA fallito per il comune '%s': %s", comune_name, exc)
                return await self.async_step_manual_select(lookup_failed=True)

            if not operatori:
                return await self.async_step_manual_select(lookup_failed=False)

            matched_keys = [
                PIVA_TO_KEY[o["piva"]] for o in operatori if o["piva"] in PIVA_TO_KEY
            ]

            if not matched_keys:
                return await self.async_step_manual_select(
                    unsupported_operator=operatori[0]["ragione_sociale"]
                )

            if len(matched_keys) > 1:
                self._candidates = matched_keys
                return await self.async_step_choose_distributor()

            self._distributor_key = matched_keys[0]
            return await self.async_step_distributor_info()

        comuni = sorted(self._tree[self._regione][self._provincia].keys())
        return self.async_show_form(
            step_id="comune",
            data_schema=vol.Schema({
                vol.Required("comune"): SelectSelector(
                    SelectSelectorConfig(options=comuni)
                )
            }),
        )

    async def async_step_choose_distributor(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._distributor_key = user_input["distributor"]
            return await self.async_step_distributor_info()

        return self.async_show_form(
            step_id="choose_distributor",
            data_schema=vol.Schema({
                vol.Required("distributor"): SelectSelector(
                    SelectSelectorConfig(options=[
                        {"value": k, "label": DISTRIBUTOR_REGISTRY[k]["display_name"]}
                        for k in self._candidates
                    ])
                )
            }),
        )

    async def async_step_manual_select(
        self,
        user_input: dict[str, Any] | None = None,
        lookup_failed: bool = False,
        unsupported_operator: str | None = None,
    ):
        if user_input is not None:
            self._distributor_key = user_input["distributor"]
            return await self.async_step_distributor_info()

        if unsupported_operator:
            description = (
                f"Il distributore rilevato da ARERA per {self._comune_name} e' "
                f"'{unsupported_operator}', non ancora supportato da questa "
                "integrazione. Se sai che il tuo comune e' comunque gestito da "
                "uno dei distributori supportati, selezionalo qui sotto."
            )
        elif lookup_failed:
            description = (
                "Non e' stato possibile contattare ARERA per determinare "
                "automaticamente il distributore. Seleziona manualmente."
            )
        else:
            description = (
                f"Nessun distributore elettrico trovato da ARERA per "
                f"{self._comune_name}. Seleziona manualmente."
            )

        return self.async_show_form(
            step_id="manual_select",
            data_schema=vol.Schema({
                vol.Required("distributor"): SelectSelector(
                    SelectSelectorConfig(options=[
                        {"value": k, "label": v["display_name"]}
                        for k, v in DISTRIBUTOR_REGISTRY.items()
                    ])
                )
            }),
            description_placeholders={"description": description},
        )

    async def async_step_distributor_info(self, user_input: dict[str, Any] | None = None):
        info = DISTRIBUTOR_REGISTRY[self._distributor_key]

        if user_input is not None:
            if info["kind"] == "pcf":
                return await self.async_step_pcf_credentials()
            return await self.async_step_edistribuzione_user()

        return self.async_show_form(
            step_id="distributor_info",
            data_schema=vol.Schema({}),
            description_placeholders={
                "distributor": info["display_name"],
                "requirements": "\n".join(f"- {r}" for r in info["required_info"]),
            },
        )

    # ------------------------------------------------------------------
    # Ramo "pcf" (Duereti/Unareti): generico, dispatcha su self._distributor_key
    # ------------------------------------------------------------------

    def _pcf_module(self):
        return DISTRIBUTOR_REGISTRY[self._distributor_key]["module"]

    async def async_step_pcf_credentials(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        modulo = self._pcf_module()

        if user_input is not None:
            errore = await modulo.async_valida_credenziali(
                self.hass, user_input[CONF_CLIENT_ID], user_input[CONF_SECRET_ID]
            )
            if errore:
                errors["base"] = errore
            else:
                self._client_id = user_input[CONF_CLIENT_ID]
                self._secret_id = user_input[CONF_SECRET_ID]
                return await self.async_step_pcf_add_pod()

        return self.async_show_form(
            step_id="pcf_credentials",
            data_schema=vol.Schema({
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_SECRET_ID): str,
            }),
            errors=errors,
            description_placeholders={
                "distributor": modulo.DISPLAY_NAME,
                "guida": (
                    f"1. Accedi al Portale Clienti Finali (PCF): {modulo.PORTAL_URL}\n"
                    "2. Assicurati di avere almeno un'identificazione validata dal "
                    "backoffice sul tuo profilo (senza questo passaggio la richiesta "
                    "di abilitazione API non compare)\n"
                    "3. Vai in \"Area POD/PDR: Interruzioni, Misure e servizi\" e cerca "
                    "l'opzione per richiedere l'abilitazione all'uso delle API\n"
                    "4. Invia la richiesta e attendi l'accettazione\n"
                    "5. Una volta approvata, Client ID e Secret ID sono visibili nella "
                    "stessa pagina (e arrivano anche via email)"
                ),
            },
        )

    async def async_step_pcf_add_pod(self, user_input: dict[str, Any] | None = None):
        """Permette di aggiungere uno o piu' POD con relativo dato fiscale."""
        errors: dict[str, str] = {}
        conflitto: str | None = None
        modulo = self._pcf_module()

        if user_input is not None:
            conflitto = pod_gia_configurato(
                self.hass, user_input["pod"], pods_gia_in_flow=self._pods
            )
            if conflitto:
                errors["pod"] = "pod_duplicato"
            else:
                errore, ticket = await modulo.async_valida_pod(
                    self.hass, self._client_id, self._secret_id, user_input["pod"], user_input["df"]
                )
                if errore:
                    errors["pod"] = errore
                else:
                    self._pods.append({"pod": user_input["pod"], "df": user_input["df"]})
                    if ticket and not self._ticket_verifica:
                        self._ticket_verifica = ticket
                    if user_input.get("aggiungi_altro"):
                        return await self.async_step_pcf_add_pod()

                    dati = {
                        "distributor": self._distributor_key,
                        "comune": self._comune_name,
                        CONF_CLIENT_ID: self._client_id,
                        CONF_SECRET_ID: self._secret_id,
                        CONF_PODS: self._pods,
                    }
                    if self._ticket_verifica:
                        giorno = (
                            dt_util.now().date() - timedelta(days=RITARDO_VERIFICA_POD_GIORNI)
                        ).isoformat()
                        dati.update({
                            CONF_PENDING_TICKET: self._ticket_verifica,
                            CONF_PENDING_DATA_DA: giorno,
                            CONF_PENDING_DATA_A: giorno,
                            CONF_PENDING_IS_BACKFILL: FASE_GIORNALIERO,
                        })
                    return self.async_create_entry(
                        title=f"{modulo.DISPLAY_NAME} ({len(self._pods)} POD)",
                        data=dati,
                    )

        schema = STEP_POD_SCHEMA.extend({vol.Optional("aggiungi_altro", default=False): bool})
        return self.async_show_form(
            step_id="pcf_add_pod",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "distributor": modulo.DISPLAY_NAME,
                "pod_conflitto": conflitto or "",
            },
        )

    # ------------------------------------------------------------------
    # Ramo E-Distribuzione: login email/password -> OTP -> selezione POD
    # ------------------------------------------------------------------

    async def async_step_edistribuzione_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            from .distributors.edistribuzione.auth import (
                EdistribuzioneAuthClient,
                EdistribuzioneInvalidCredentials,
                EdistribuzioneParsingError,
            )

            # Dedicata (non la sessione condivisa async_get_clientsession):
            # questo login passa per una catena di redirect Salesforce che
            # dipende da un cookie di sessione impostato a meta' strada. La
            # sessione condivisa HA e' usata da tutte le integrazioni
            # dell'istanza e non garantisce che quel cookie venga
            # persistito/reinviato in modo affidabile per questo dominio -
            # il sintomo osservato e' un loop di redirect infinito
            # (aiohttp.TooManyRedirects) perche' il server continua a
            # ripresentare la pagina "imposta il cookie" non vedendolo mai
            # tornare indietro. Creata una sola volta e riusata tra i
            # retry di questo stesso flow, cosi' i cookie accumulati non
            # si perdono da un tentativo all'altro.
            if self._edistribuzione_session is None:
                self._edistribuzione_session = async_create_clientsession(self.hass)
            self._edistribuzione_auth = EdistribuzioneAuthClient(self._edistribuzione_session)

            try:
                await self._edistribuzione_auth.async_begin_login(
                    user_input["email"], user_input["password"]
                )
            except EdistribuzioneInvalidCredentials:
                errors["base"] = "invalid_auth"
            except EdistribuzioneParsingError:
                _LOGGER.exception("Parsing della pagina di login E-Distribuzione fallito")
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - qualunque altro errore imprevisto
                # aiohttp.ClientError, timeout, risposta non-JSON dove ce ne
                # aspettavamo una, o qualunque altra cosa che auth.py non
                # incapsula nelle sue eccezioni dedicate: meglio mostrare un
                # errore nel form (e loggare il traceback completo per
                # capire cosa e' successo davvero) che far esplodere lo step
                # con lo "Unknown error occurred" generico di Home Assistant.
                _LOGGER.exception(
                    "Errore imprevisto durante il login E-Distribuzione"
                )
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_edistribuzione_otp()

        return self.async_show_form(
            step_id="edistribuzione_user",
            data_schema=vol.Schema({
                vol.Required("email"): str,
                vol.Required("password"): str,
            }),
            errors=errors,
        )

    async def async_step_edistribuzione_otp(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            from .distributors.edistribuzione.auth import (
                EdistribuzioneInvalidOtp,
                EdistribuzioneParsingError,
            )

            try:
                tokens = await self._edistribuzione_auth.async_submit_otp(user_input["otp"])
            except EdistribuzioneInvalidOtp:
                errors["base"] = "invalid_otp"
            except EdistribuzioneParsingError:
                _LOGGER.exception("Parsing della pagina OTP E-Distribuzione fallito")
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - vedi commento in edistribuzione_user
                _LOGGER.exception(
                    "Errore imprevisto durante lo scambio del codice OTP E-Distribuzione"
                )
                errors["base"] = "cannot_connect"
            else:
                self._edistribuzione_access_token = tokens.access_token
                self._edistribuzione_refresh_token = tokens.refresh_token
                return await self.async_step_edistribuzione_pod()

        return self.async_show_form(
            step_id="edistribuzione_otp",
            data_schema=vol.Schema({vol.Required("otp"): str}),
            errors=errors,
        )

    async def async_step_edistribuzione_pod(self, user_input: dict[str, Any] | None = None):
        from .distributors.edistribuzione.api import EdistribuzioneApiClient
        from .distributors.edistribuzione.const import CONF_PODS, CONF_REFRESH_TOKEN

        session = async_get_clientsession(self.hass)
        api = EdistribuzioneApiClient(session, self._edistribuzione_access_token)

        if not self._edistribuzione_pods:
            try:
                self._edistribuzione_pods = await api.async_get_supplies()
            except Exception:  # noqa: BLE001 - vedi commento in edistribuzione_user
                # Login e OTP sono gia' andati a buon fine qui: un OTP e'
                # utilizzabile una sola volta, quindi non ha senso far
                # ripresentare il form di questo step (un retry non
                # risolverebbe nulla senza rifare login+OTP da capo).
                # Meglio abortire con un messaggio chiaro che dire
                # esplicitamente di ricominciare, invece di un crash.
                _LOGGER.exception(
                    "Errore imprevisto nel recupero dei POD E-Distribuzione"
                )
                return self.async_abort(reason="edistribuzione_supplies_failed")

        def crea_entry(pods: list[str]):
            titolo = pods[0] if len(pods) == 1 else f"{len(pods)} POD"
            return self.async_create_entry(
                title=f"E-Distribuzione ({titolo})",
                data={
                    "distributor": "edistribuzione",
                    "comune": self._comune_name,
                    CONF_PODS: pods,
                    CONF_REFRESH_TOKEN: self._edistribuzione_refresh_token,
                },
            )

        if not self._edistribuzione_pods:
            return self.async_abort(reason="no_pods_found")

        # Con un solo POD sull'account non serve far scegliere: lo
        # selezioniamo subito, com'era anche prima di supportare più POD.
        if len(self._edistribuzione_pods) == 1:
            return crea_entry([self._edistribuzione_pods[0]["IdPod"]])

        if user_input is not None:
            scelti = user_input[CONF_PODS]
            if not scelti:
                return self.async_show_form(
                    step_id="edistribuzione_pod",
                    data_schema=self._schema_multi_pod(),
                    errors={"pods": "nessun_pod_selezionato"},
                )
            return crea_entry(scelti)

        return self.async_show_form(
            step_id="edistribuzione_pod",
            data_schema=self._schema_multi_pod(),
        )

    def _schema_multi_pod(self) -> vol.Schema:
        from .distributors.edistribuzione.const import CONF_PODS

        pod_ids = [p["IdPod"] for p in self._edistribuzione_pods]
        opzioni = [
            {"value": p["IdPod"], "label": _etichetta_pod(p)}
            for p in self._edistribuzione_pods
        ]
        return vol.Schema({
            vol.Required(CONF_PODS, default=pod_ids): selector.SelectSelector(
                selector.SelectSelectorConfig(options=opzioni, multiple=True)
            )
        })

    # ------------------------------------------------------------------
    # Reauth E-Distribuzione: stesso login+OTP dell'onboarding iniziale,
    # ma niente selezione POD (la entry esistente ha gia' i suoi) - alla
    # fine aggiorna il refresh_token sulla entry esistente invece di
    # crearne una nuova. Step separati da async_step_edistribuzione_user/
    # _otp (non riusati direttamente) per non dover far diramare quelli
    # tra "crea nuova entry" e "aggiorna quella esistente", che avrebbe
    # significato toccare codice gia' testato in produzione per aggiungere
    # un ramo usato raramente.
    # ------------------------------------------------------------------

    async def async_step_edistribuzione_reauth_user(
        self, user_input: dict[str, Any] | None = None
    ):
        errors: dict[str, str] = {}

        if user_input is not None:
            from .distributors.edistribuzione.auth import (
                EdistribuzioneAuthClient,
                EdistribuzioneInvalidCredentials,
                EdistribuzioneParsingError,
            )

            session = async_get_clientsession(self.hass)
            self._edistribuzione_auth = EdistribuzioneAuthClient(session)

            try:
                await self._edistribuzione_auth.async_begin_login(
                    user_input["email"], user_input["password"]
                )
            except EdistribuzioneInvalidCredentials:
                errors["base"] = "invalid_auth"
            except EdistribuzioneParsingError:
                _LOGGER.exception(
                    "Parsing della pagina di login E-Distribuzione fallito (reauth)"
                )
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - vedi commento in edistribuzione_user
                _LOGGER.exception(
                    "Errore imprevisto durante il login E-Distribuzione (reauth)"
                )
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_edistribuzione_reauth_otp()

        return self.async_show_form(
            step_id="edistribuzione_reauth_user",
            data_schema=vol.Schema({
                vol.Required("email"): str,
                vol.Required("password"): str,
            }),
            errors=errors,
            description_placeholders={"pod_correnti": ", ".join(
                self._reauth_entry.data.get("pods", [])
            )},
        )

    async def async_step_edistribuzione_reauth_otp(
        self, user_input: dict[str, Any] | None = None
    ):
        errors: dict[str, str] = {}

        if user_input is not None:
            from .distributors.edistribuzione.auth import (
                EdistribuzioneInvalidOtp,
                EdistribuzioneParsingError,
            )

            try:
                tokens = await self._edistribuzione_auth.async_submit_otp(user_input["otp"])
            except EdistribuzioneInvalidOtp:
                errors["base"] = "invalid_otp"
            except EdistribuzioneParsingError:
                _LOGGER.exception(
                    "Parsing della pagina OTP E-Distribuzione fallito (reauth)"
                )
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Errore imprevisto durante lo scambio del codice OTP E-Distribuzione (reauth)"
                )
                errors["base"] = "cannot_connect"
            else:
                from .distributors.edistribuzione.const import CONF_REFRESH_TOKEN

                nuovi_dati = {
                    **self._reauth_entry.data,
                    CONF_REFRESH_TOKEN: tokens.refresh_token,
                }
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=nuovi_dati)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="edistribuzione_reauth_otp",
            data_schema=vol.Schema({vol.Required("otp"): str}),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Reauth (generico, dispatcha sul distributore della entry esistente)
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._distributor_key = self._reauth_entry.data["distributor"]
        kind = DISTRIBUTOR_REGISTRY[self._distributor_key]["kind"]
        if kind == "pcf":
            return await self.async_step_reauth_confirm()
        if kind == "edistribuzione":
            return await self.async_step_edistribuzione_reauth_user()
        return self.async_abort(reason="reauth_not_supported")

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        modulo = self._pcf_module()

        if user_input is not None:
            errore = await modulo.async_valida_credenziali(
                self.hass, user_input[CONF_CLIENT_ID], user_input[CONF_SECRET_ID]
            )
            if errore:
                errors["base"] = errore
            else:
                nuovi_dati = {
                    **self._reauth_entry.data,
                    CONF_CLIENT_ID: user_input[CONF_CLIENT_ID],
                    CONF_SECRET_ID: user_input[CONF_SECRET_ID],
                }
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=nuovi_dati)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_SECRET_ID): str,
            }),
            errors=errors,
            description_placeholders={
                "distributor": modulo.DISPLAY_NAME,
                "portal_url": modulo.PORTAL_URL,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> ContatoreLettureOptionsFlow:
        return ContatoreLettureOptionsFlow()


class ContatoreLettureOptionsFlow(config_entries.OptionsFlow):
    """Aggiungi/rimuovi POD e imposta l'orario, dopo la configurazione iniziale.

    Generico: legge il distributore dalla config entry (self.config_entry.data)
    per sapere quale modulo usare per validare i POD. Per E-Distribuzione (kind
    diverso da "pcf") non offre nulla di specifico oggi: solo un abort chiaro.
    """

    def _modulo(self):
        distributor_key = self.config_entry.data["distributor"]
        return DISTRIBUTOR_REGISTRY[distributor_key]["module"]

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        distributor_key = self.config_entry.data["distributor"]
        kind = DISTRIBUTOR_REGISTRY[distributor_key]["kind"]
        if kind == "pcf":
            return self.async_show_menu(
                step_id="init",
                menu_options=["aggiungi_pod", "rimuovi_pod", "orario"],
            )
        if kind == "edistribuzione":
            return self.async_show_menu(
                step_id="init",
                menu_options=[
                    "edistribuzione_aggiungi_pod",
                    "edistribuzione_rimuovi_pod",
                    "orario",
                ],
            )
        return self.async_abort(reason="options_not_supported")

    async def async_step_orario(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            ora = int(user_input[CONF_ORA_RICHIESTA])
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_ORA_RICHIESTA: ora}
            )

        attuale = self.config_entry.options.get(CONF_ORA_RICHIESTA, ORA_MINIMA_RICHIESTA)
        return self.async_show_form(
            step_id="orario",
            data_schema=vol.Schema({
                vol.Required(CONF_ORA_RICHIESTA, default=attuale): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=23, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                )
            }),
            description_placeholders={"ora_attuale": str(attuale)},
        )

    async def async_step_aggiungi_pod(self, user_input: dict[str, Any] | None = None):
        pods = list(self.config_entry.data.get(CONF_PODS, []))
        errors: dict[str, str] = {}
        conflitto: str | None = None
        modulo = self._modulo()

        if user_input is not None:
            conflitto = pod_gia_configurato(
                self.hass,
                user_input["pod"],
                pods_gia_in_flow=pods,
                escludi_entry_id=self.config_entry.entry_id,
            )
            if conflitto:
                errors["pod"] = "pod_duplicato"
            else:
                errore, _ticket = await modulo.async_valida_pod(
                    self.hass,
                    self.config_entry.data[CONF_CLIENT_ID],
                    self.config_entry.data[CONF_SECRET_ID],
                    user_input["pod"],
                    user_input["df"],
                )
                if errore:
                    errors["pod"] = errore
                    return self.async_show_form(
                        step_id="aggiungi_pod",
                        data_schema=STEP_POD_SCHEMA,
                        errors=errors,
                        description_placeholders={
                            "pod_correnti": ", ".join(p["pod"] for p in pods) or "nessuno",
                            "pod_conflitto": "",
                        },
                    )
                pods.append({"pod": user_input["pod"], "df": user_input["df"]})
                new_data = {**self.config_entry.data, CONF_PODS: pods}
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="aggiungi_pod",
            data_schema=STEP_POD_SCHEMA,
            errors=errors,
            description_placeholders={
                "pod_correnti": ", ".join(p["pod"] for p in pods) or "nessuno",
                "pod_conflitto": conflitto or "",
            },
        )

    async def async_step_rimuovi_pod(self, user_input: dict[str, Any] | None = None):
        pods = list(self.config_entry.data.get(CONF_PODS, []))

        if not pods:
            return self.async_abort(reason="nessun_pod")

        if user_input is not None:
            da_rimuovere = set(user_input.get("pods_da_rimuovere", []))
            if len(da_rimuovere) >= len(pods):
                return self.async_show_form(
                    step_id="rimuovi_pod",
                    data_schema=self._schema_rimuovi(pods),
                    errors={"pods_da_rimuovere": "non_puoi_rimuoverli_tutti"},
                )
            pods_rimasti = [p for p in pods if p["pod"] not in da_rimuovere]
            new_data = {**self.config_entry.data, CONF_PODS: pods_rimasti}
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(step_id="rimuovi_pod", data_schema=self._schema_rimuovi(pods))

    @staticmethod
    def _schema_rimuovi(pods: list[dict]) -> vol.Schema:
        opzioni = [p["pod"] for p in pods]
        return vol.Schema({
            vol.Required("pods_da_rimuovere", default=[]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=opzioni, multiple=True)
            )
        })

    # ------------------------------------------------------------------
    # E-Distribuzione: aggiungi/rimuovi POD
    #
    # A differenza di PCF, qui non serve un dato fiscale per POD (sono
    # tutti gia' sull'account autenticato): per aggiungerne uno nuovo
    # basta rifare il refresh del token e richiedere di nuovo l'elenco
    # POD dell'account, senza richiedere email/password/OTP di nuovo.
    # ------------------------------------------------------------------

    async def async_step_edistribuzione_aggiungi_pod(
        self, user_input: dict[str, Any] | None = None
    ):
        from .distributors.edistribuzione.api import EdistribuzioneApiClient
        from .distributors.edistribuzione.auth import EdistribuzioneAuthClient
        from .distributors.edistribuzione.const import CONF_PODS, CONF_REFRESH_TOKEN

        pods_attuali = list(self.config_entry.data.get(CONF_PODS, []))

        if user_input is not None:
            nuovi = user_input.get("pods_da_aggiungere", [])
            if not nuovi:
                return self.async_abort(reason="nessun_pod_selezionato")
            pods_finali = pods_attuali + [p for p in nuovi if p not in pods_attuali]
            new_data = {**self.config_entry.data, CONF_PODS: pods_finali}
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        session = async_get_clientsession(self.hass)
        auth = EdistribuzioneAuthClient(session)
        try:
            tokens = await auth.async_refresh_access_token(
                self.config_entry.data[CONF_REFRESH_TOKEN]
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Refresh del token E-Distribuzione fallito nelle opzioni")
            return self.async_abort(reason="edistribuzione_refresh_failed")

        api = EdistribuzioneApiClient(session, tokens.access_token)
        try:
            tutti_pod = await api.async_get_supplies()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Recupero POD E-Distribuzione fallito nelle opzioni")
            return self.async_abort(reason="edistribuzione_supplies_failed")

        pods_disponibili = [p for p in tutti_pod if p["IdPod"] not in pods_attuali]
        if not pods_disponibili:
            return self.async_abort(reason="nessun_pod_da_aggiungere")

        opzioni = [{"value": p["IdPod"], "label": _etichetta_pod(p)} for p in pods_disponibili]

        return self.async_show_form(
            step_id="edistribuzione_aggiungi_pod",
            data_schema=vol.Schema({
                vol.Required("pods_da_aggiungere", default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=opzioni, multiple=True)
                )
            }),
            description_placeholders={
                "pod_correnti": ", ".join(pods_attuali) or "nessuno",
            },
        )

    async def async_step_edistribuzione_rimuovi_pod(
        self, user_input: dict[str, Any] | None = None
    ):
        from .distributors.edistribuzione.const import CONF_PODS

        pods = list(self.config_entry.data.get(CONF_PODS, []))
        if not pods:
            return self.async_abort(reason="nessun_pod")

        if user_input is not None:
            da_rimuovere = set(user_input.get("pods_da_rimuovere", []))
            if len(da_rimuovere) >= len(pods):
                return self.async_show_form(
                    step_id="edistribuzione_rimuovi_pod",
                    data_schema=self._schema_rimuovi_edistribuzione(pods),
                    errors={"pods_da_rimuovere": "non_puoi_rimuoverli_tutti"},
                )
            pods_rimasti = [p for p in pods if p not in da_rimuovere]
            new_data = {**self.config_entry.data, CONF_PODS: pods_rimasti}
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="edistribuzione_rimuovi_pod",
            data_schema=self._schema_rimuovi_edistribuzione(pods),
        )

    @staticmethod
    def _schema_rimuovi_edistribuzione(pods: list[str]) -> vol.Schema:
        return vol.Schema({
            vol.Required("pods_da_rimuovere", default=[]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=pods, multiple=True)
            )
        })
