"""Authentication client for e-Distribuzione (private.e-distribuzione.it).

Il portale risponde su due host della stessa org Salesforce (vedi const.py):
quando il dominio personalizzato risponde con la pagina antibot di Imperva,
login e refresh del token ripartono sul dominio canonico
(edistribuzione.my.site.com). Ogni tentativo riparte dal principale, cosi'
si torna da soli al dominio ufficiale quando il blocco viene tolto.

The login itself is a standard Salesforce OAuth2 Authorization Code flow with
PKCE. The friction is in the middle: credentials are submitted via an Aura
("Lightning") remote action, and the OTP step is a classic Salesforce
Visualforce "Login Flow" interview page using ViewState/RichFaces AJAX.

Both of those are UI implementation details, not a stable public API, so this
module scrapes HTML/JSON fragments with regexes. It WILL break if Enel changes
their Experience Cloud template, RichFaces version, or field API names.
Treat everything in `_extract_*` as the first thing to check if login starts
failing.

Once we have a refresh_token, none of this fragile code needs to run again:
async_refresh_access_token() only talks to the standard OAuth2 token endpoint.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_lib
import json
import logging
import re
import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import aiohttp

from .const import (
    AURA_ENDPOINT,
    LOGINFLOW_URL,
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPE,
    OAUTH_TOKEN_URL,
    SF_HOST_DIRETTO,
    SF_HOST_PRINCIPALE,
    SF_HOSTS,
)

_LOGGER = logging.getLogger(__name__)

_MOBILE_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"


# Frammenti con cui il portale segnala che l'account ha troppe sessioni
# aperte contemporaneamente ("Hai superato il numero di sessioni simultanee
# consentite", osservato sul sito il 13/09/2026 - segnalato nell'issue #2).
# Va distinto da credenziali sbagliate e da un cambio di markup: in questo
# stato il login non prosegue e l'OTP non viene nemmeno inviato, quindi
# l'utente resterebbe ad aspettare un codice che non arriva.
_MARCATORI_TROPPE_SESSIONI = (
    "sessioni simultanee",
    "sessioni contemporanee",
    "numero di sessioni",
    "sessioni consentite",
)

# Pagine della protezione antibot di Imperva (Incapsula), che risponde al
# posto degli endpoint di login e token quando classifica il traffico come
# automatico: il blocco 403 "Request unsuccessful. Incapsula incident ID"
# (visto il 10/10/2026, issue #8) e il controllo JavaScript "Pardon Our
# Interruption". Solo marcatori di queste due pagine: lo script
# _Incapsula_Resource puo' comparire anche nelle pagine normali di un sito
# protetto.
_MARCATORI_ANTIBOT = (
    "pardon our interruption",
    "incapsula incident id",
)

# Testo con cui la risposta al primo submit del form conferma di avere
# spedito il codice (confermato su HAR reale il 20/08/2026: "Abbiamo inviato
# un codice a 5 cifre al tuo indirizzo email"). Piu' varianti perche' il
# canale (email/SMS) e la formulazione cambiano da account ad account: qui
# l'assenza di conferma NON viene trattata come errore fatale (romperebbe
# login altrimenti validi con una formulazione diversa), solo segnalata.
_MARCATORI_OTP_INVIATO = (
    "abbiamo inviato",
    "inviato un codice",
    "codice a 5 cifre",
    "codice è stato inviato",
    "nuovo codice",
)


# Etichette del pulsante che APPROVA sulla schermata di consenso OAuth.
# Sulla pagina reale (catturata il 13/09/2026, issue #2) i due pulsanti
# hanno lo STESSO name="save" e si distinguono solo per il valore:
# "Consenti" approva, " Nega " (con gli spazi) rifiuta - sbagliare pulsante
# significa negare l'autorizzazione all'integrazione, quindi il valore va
# confrontato per intero, non cercato come sottostringa.
_ETICHETTE_CONSENSO = ("consenti", "allow", "approve", "autorizza", "accetta")


def _contiene(html: str, marcatori: tuple[str, ...]) -> bool:
    testo = html.lower()
    return any(marcatore in testo for marcatore in marcatori)


def _scrivi_pagina_debug(html: str, nome_file: str) -> None:
    try:
        percorso = Path(nome_file)
        percorso.write_text(html, encoding="utf-8")
    except OSError as exc:
        _LOGGER.debug("Impossibile salvare %s su disco: %s", nome_file, exc)
        return
    _LOGGER.error("Pagina completa salvata in %s", percorso.resolve())


def _salva_pagina_debug(html: str, nome_file: str) -> None:
    """Scrive la pagina su disco accanto a configuration.yaml (best-effort:
    se il filesystem non e' scrivibile in questo contesto non blocca nulla)
    e logga il percorso, cosi' e' richiedibile all'utente in una
    segnalazione senza dovergli far catturare una HAR.

    La scrittura e' delegata a un executor: qui siamo dentro l'event loop di
    Home Assistant, che segnala l'I/O sincrono con "Detected blocking call
    to write_text" (visto in un log reale il 13/09/2026, prodotto dalla
    0.6.1 su consent_page_debug.html). Fuori da un event loop (test,
    scripts/verify_edistribuzione_login.py) scrive direttamente.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _scrivi_pagina_debug(html, nome_file)
        return
    # Deliberatamente non atteso: e' un dump diagnostico best-effort, non
    # deve rallentare ne' far fallire il login se il disco e' lento o pieno
    # (_scrivi_pagina_debug logga da se' l'esito).
    loop.run_in_executor(None, _scrivi_pagina_debug, html, nome_file)


def _log_parsing_failure_context(
    html: str, campo_cercato: str, nome_file_debug: str = "otp_page_debug.html"
) -> None:
    """Logga titolo + un'anteprima della pagina quando un campo atteso non
    si trova - serve a distinguere "regex sbagliato" (il campo c'e' ma in
    forma diversa) da "pagina completamente diversa da quella attesa"
    (es. errore, OTP saltato perche' il dispositivo e' gia' fidato, sessione
    scaduta) senza dover chiedere un'altra cattura per scoprirlo.

    Salva anche la pagina intera su disco (best-effort - se il filesystem
    non e' scrivibile in questo contesto, es. dentro il container di Home
    Assistant, non blocca nulla, si limita a non salvare)."""
    title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else "(nessun <title> trovato)"
    _LOGGER.error(
        "Campo '%s' non trovato. Titolo pagina: %r. Lunghezza: %d caratteri. "
        "Primi 300 caratteri: %r",
        campo_cercato,
        title,
        len(html),
        html[:300],
    )
    _salva_pagina_debug(html, nome_file_debug)

_MAX_REDIRECT_HOPS = 15


class EdistribuzioneAuthError(Exception):
    """Generic authentication failure."""


class EdistribuzioneInvalidCredentials(EdistribuzioneAuthError):
    """Wrong email/password."""


class EdistribuzioneInvalidOtp(EdistribuzioneAuthError):
    """Wrong or expired OTP code."""


class EdistribuzioneTroppeSessioni(EdistribuzioneAuthError):
    """L'account ha gia' troppe sessioni aperte: il portale rifiuta il login
    prima di inviare qualunque OTP.

    Non e' un problema di credenziali ne' di markup cambiato: finche' le
    altre sessioni non vengono chiuse o non scadono (app ufficiale, sito,
    tentativi precedenti di questa stessa integrazione) non c'e' niente da
    reinserire nel form, va liberata una sessione e riprovato.
    """


class EdistribuzioneBloccoAntibot(EdistribuzioneAuthError):
    """Il portale ha risposto con la pagina anti-bot di Imperva invece che con
    quella attesa: l'accesso automatico e' bloccato lato server.

    Visto in un log reale l'08/10/2026: "Pardon Our Interruption" con HTTP 200
    direttamente su /services/oauth2/authorize, nessun redirect; il 10/10/2026
    anche il 403 "Incapsula incident ID". Non e' un cambio di markup ne' un
    problema di credenziali. Arriva al chiamante solo se sono bloccati
    entrambi gli host (vedi const.SF_HOSTS), e va riprovato piu' tardi.
    """


def _verifica_non_bloccato(testo: str) -> None:
    """Alza EdistribuzioneBloccoAntibot se la risposta e' la pagina antibot."""
    if _contiene(testo, _MARCATORI_ANTIBOT):
        raise EdistribuzioneBloccoAntibot(
            "E-Distribuzione ha risposto con la pagina antibot (Imperva)"
        )


async def _leggi_testo(resp: aiohttp.ClientResponse) -> str:
    """Corpo della risposta, dopo aver escluso la pagina antibot."""
    testo = await resp.text()
    _verifica_non_bloccato(testo)
    return testo


def _identita(url: str) -> str:
    return url


class EdistribuzioneParsingError(EdistribuzioneAuthError):
    """The scraped page didn't contain what we expected.

    Most likely cause: Enel changed something in their Salesforce site and
    the regexes below need updating. Capture a fresh HAR and compare.
    """


async def _get_following_redirects(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    riscrivi=_identita,
) -> aiohttp.ClientResponse:
    """GET seguendo i redirect a mano invece di allow_redirects=True.

    Necessario perche' la catena di redirect di /services/oauth2/authorize
    passa per un parametro 'startURL' che contiene un URL annidato
    percent-encoded (%2F, %3F, ...). Lasciando che aiohttp gestisca i
    redirect da solo (allow_redirects=True), il comportamento dipende
    dall'opzione 'requote_redirect_url' della ClientSession (che puo'
    ri-codificare il Location gia' codificato, alterandolo ad ogni hop) e
    puo' entrare in un loop infinito fino a TooManyRedirects - riprodotto e
    confermato il 20/08/2026 con un HAR reale. Seguendo i redirect
    esplicitamente con encoded=True sull'URL del prossimo hop, il valore
    del Location non viene mai ri-processato: la stessa catena che con
    allow_redirects=True va in loop qui si risolve in 3 hop.

    'riscrivi' riporta l'URL di ogni hop sull'host attivo (vedi
    EdistribuzioneAuthClient._su_host): un Location puo' puntare al dominio
    personalizzato dietro Imperva, e seguirlo cosi' com'e' farebbe atterrare
    sulla pagina antibot invece che a destinazione.

    Ritorna la risposta finale (status < 300, nessun altro Location);
    il chiamante e' responsabile di chiudere/consumare 'resp' come al solito.
    """
    resp = await session.get(url, params=params, headers=headers, allow_redirects=False)

    for _ in range(_MAX_REDIRECT_HOPS):
        location = resp.headers.get("Location")
        if location is None:
            return resp

        resp.close()
        location_url = aiohttp.client.URL(location, encoded=True)
        next_url = location_url if location_url.is_absolute() else resp.url.join(location_url)
        next_url = aiohttp.client.URL(riscrivi(str(next_url)), encoded=True)
        resp = await session.get(next_url, headers=headers, allow_redirects=False)

    resp.close()
    raise EdistribuzioneAuthError(
        f"Troppi redirect (>{_MAX_REDIRECT_HOPS}) seguendo {url}: possibile "
        "cambiamento lato Salesforce nella struttura dei redirect di login."
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@dataclass
class _LoginFlowState:
    """Everything we need to carry between the OTP page and its submission."""

    fwuid: str | None = None
    aura_token: str | None = None
    view_state: str | None = None
    view_state_version: str | None = None
    view_state_mac: str | None = None
    view_state_csrf: str | None = None
    form_action_url: str | None = None
    # Fields captured under `thePage:j_id2:i:f:...` vary per deploy; we keep
    # the raw prefix so we don't have to hardcode Salesforce's generated IDs.
    otp_field_name: str | None = None
    resend_field_name: str | None = None
    # La checkbox visibile "Richiedi nuovo OTP", gemella del campo hidden
    # sopra: serve solo per il reinvio esplicito (async_resend_otp), dove
    # riproduciamo il submit di un browser con la casella spuntata.
    resend_checkbox_field_name: str | None = None
    next_field_name: str | None = None


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str
    instance_url: str
    id_token: str | None = None
    raw: dict = field(default_factory=dict)


class EdistribuzioneAuthClient:
    """Drives the login -> OTP -> OAuth code -> token exchange sequence."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._code_verifier: str | None = None
        self._oauth_state: str | None = None
        self._flow = _LoginFlowState()
        # Host Salesforce attivo: parte dal principale e passa al diretto solo
        # se il principale risponde con la pagina antibot (vedi
        # async_begin_login/async_refresh_access_token).
        self._host = SF_HOST_PRINCIPALE
        # True/False dopo async_begin_login()/async_resend_otp() a seconda
        # che il portale abbia confermato l'invio del codice; None finche'
        # non ci si e' arrivati. Il config flow lo usa per avvisare l'utente
        # invece di lasciarlo aspettare un OTP che non arrivera' mai
        # (issue #2).
        self.otp_invio_confermato: bool | None = None

    @property
    def _origin(self) -> str:
        return f"https://{self._host}"

    def _su_host(self, url: str) -> str:
        """Riporta un URL sull'host attivo.

        Le costanti del modulo e gli URL generati dal server puntano al
        dominio personalizzato: dopo il passaggio all'host diretto vanno
        riscritti tutti, altrimenti la richiesta successiva tornerebbe sul
        dominio bloccato. URL relativi o di altri host passano invariati.
        """
        for host in SF_HOSTS:
            prefisso = f"https://{host}"
            if url.startswith(prefisso):
                return self._origin + url[len(prefisso):]
        return url

    # -- Step 1: authorize + credentials -------------------------------------

    async def async_begin_login(self, email: str, password: str) -> None:
        """Submit email/password. Raises if credentials are rejected.

        On success, internal state is primed so that async_submit_otp() can
        complete the flow. This always assumes an OTP step follows, matching
        every capture we've seen so far.

        Prova prima l'host principale; se e' bloccato dalla pagina antibot
        riparte da capo sull'host diretto. Il blocco arriva al primo passo,
        prima che parta qualunque OTP, quindi ripartire non spreca codici;
        gli step successivi (async_submit_otp) restano sull'host che ha
        funzionato.
        """
        self._host = SF_HOST_PRINCIPALE
        try:
            await self._async_begin_login_su_host(email, password)
        except EdistribuzioneBloccoAntibot:
            if self._host == SF_HOST_DIRETTO:
                raise
            _LOGGER.info(
                "Login bloccato dall'antibot su %s, uso %s",
                SF_HOST_PRINCIPALE,
                SF_HOST_DIRETTO,
            )
            self._host = SF_HOST_DIRETTO
            self._flow = _LoginFlowState()
            self.otp_invio_confermato = None
            await self._async_begin_login_su_host(email, password)

    async def _async_begin_login_su_host(self, email: str, password: str) -> None:
        self._code_verifier, code_challenge = _make_pkce_pair()
        self._oauth_state = _b64url(secrets.token_bytes(16))

        headers = {"User-Agent": _MOBILE_USER_AGENT}
        params = {
            "prompt": "login",
            "nonce": _b64url(secrets.token_bytes(16)),
            "display": "touch",
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "login_hint": email,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "redirect_uri": OAUTH_REDIRECT_URI,
            "client_id": OAUTH_CLIENT_ID,
            "state": self._oauth_state,
        }

        # Segue i redirect a mano (vedi _get_following_redirects) invece di
        # allow_redirects=True: quest'ultimo puo' entrare in un loop
        # infinito su questa catena specifica per via di un parametro
        # ('startURL') che contiene un URL annidato gia' percent-encoded.
        resp = await _get_following_redirects(
            self._session,
            self._su_host(OAUTH_AUTHORIZE_URL),
            params=params,
            headers=headers,
            riscrivi=self._su_host,
        )
        login_page_html = await resp.text()
        # Il parametro 'startURL' della pagina su cui atterriamo contiene un
        # token 'source=...' generato dal server al primo hop
        # (oauth2/authorize), che il server pretende di riavere indietro nel
        # campo 'startUrl' dentro 'message' per validare che il login
        # appartenga allo stesso flusso di autorizzazione - confermato
        # confrontando con una richiesta reale riuscita il 20/08/2026, dove
        # mandare un path nudo (senza questo token) produceva lo stesso
        # AuraClientInputException generico osservato più volte.
        start_url_value = resp.url.query.get(
            "startURL", "/PortaleClienti/setup/secur/RemoteAccessAuthorizationPage.apexp"
        )
        aura_page_uri = resp.url.path_qs
        referer = str(resp.url)
        landing_status = resp.status
        # Senza query string: contiene login_hint (l'email dell'utente) e
        # finirebbe nei log che gli utenti incollano nelle issue.
        landing_url = str(resp.url.with_query(None))
        resp.close()

        if _contiene(login_page_html, _MARCATORI_ANTIBOT):
            # Sull'host principale il blocco e' atteso e async_begin_login
            # ripiega da solo sul diretto: avviso e pagina di debug servono
            # solo se e' bloccato anche quello, cioe' quando il login
            # fallisce davvero.
            if self._host == SF_HOST_DIRETTO:
                _LOGGER.warning(
                    "E-Distribuzione ha risposto con la pagina anti-bot Imperva "
                    "(HTTP %s su %s) invece che con la pagina di login",
                    landing_status,
                    landing_url,
                )
                _salva_pagina_debug(login_page_html, "login_page_debug.html")
            raise EdistribuzioneBloccoAntibot(
                f"Richiesta bloccata dalla protezione anti-bot del portale "
                f"(HTTP {landing_status} su {landing_url})"
            )

        try:
            self._flow.fwuid = self._extract_fwuid(login_page_html)
        except EdistribuzioneParsingError as err:
            # Distingue "portale giu'/in manutenzione" (status 5xx, pagina
            # di errore, URL di atterraggio diverso dalla pagina di login)
            # da "markup cambiato" (pagina di login regolare ma fwuid in una
            # forma che i regex non riconoscono) senza dover chiedere una
            # cattura HAR per scoprirlo.
            _log_parsing_failure_context(
                login_page_html, "fwuid su pagina di login", "login_page_debug.html"
            )
            raise EdistribuzioneParsingError(
                f"fwuid not found on login page (HTTP {landing_status}, "
                f"atterrati su {landing_url}, pagina lunga "
                f"{len(login_page_html)} caratteri - vedi log per un'anteprima)"
            ) from err
        loaded = self._extract_loaded(login_page_html)
        # aura.token: confermato su una HAR reale con login riuscito il
        # 20/08/2026 che il client invia letteralmente la stringa "null"
        # (non un token vero) per questa azione specifica, e il server la
        # accetta ("state":"SUCCESS"). Le due ipotesi precedenti (token nel
        # body HTML, poi token in un cookie __Host-ERIC_PROD*) erano
        # entrambe sbagliate - quel cookie non compare da nessuna parte in
        # una sessione fresca. Verosimilmente non c'e' ancora una sessione
        # autenticata da proteggere via CSRF a questo punto del flusso,
        # quindi il server non pretende un token reale per loginUser.
        self._flow.aura_token = "null"

        # Costruito con json.dumps invece di concatenazione di stringhe:
        # oltre a essere più leggibile, evita JSON malformato se
        # email/password contenessero virgolette o backslash (mai
        # verificato prima, ma con tutti i problemi di "JSON non valido"
        # incontrati finora meglio non lasciarlo al caso).
        message = json.dumps({
            "actions": [{
                "id": "1;a",
                "descriptor": "apex://PED_LoginController/ACTION$loginUser",
                "callingDescriptor": "markup://c:PED_Login",
                "params": {
                    "username": email,
                    "password": password,
                    "startUrl": start_url_value,
                },
            }]
        })
        # 'loaded' NON puo' essere {} (vuoto): il server risponde con un
        # generico AuraClientInputException ("Unexpected request input")
        # se non corrisponde a quanto si aspetta - confermato confrontando
        # con una richiesta reale riuscita il 20/08/2026, dove conteneva un
        # riferimento alla versione del componente caricato
        # (es. {"APPLICATION@markup://siteforce:loginApp2":"1628_..."}).
        aura_context = json.dumps({
            "mode": "PROD",
            "fwuid": self._flow.fwuid,
            "app": "siteforce:loginApp2",
            "loaded": json.loads(loaded),
            "dn": [],
            "globals": {},
            "uad": True,
        })
        data = {
            "message": message,
            "aura.context": aura_context,
            "aura.pageURI": aura_page_uri,
            "aura.token": self._flow.aura_token,
        }

        # X-SFDC-Page-Scope-Id: mai visto in nessuna risposta del server in
        # tutta la HAR analizzata, solo nelle richieste - e' verosimilmente
        # generato lato client (un ID di correlazione per la pagina/sessione,
        # riusato identico su tutte le chiamate Aura), non qualcosa da
        # estrarre. Generato qui un UUID plausibile, non provato necessario
        # da solo ma coerente con quanto osservato in una richiesta reale.
        #
        # Origin/Referer/Content-Type aggiunti per la stessa ragione: non
        # individualmente confermati come causa del rifiuto, ma presenti
        # nella richiesta reale riuscita e a rischio zero da aggiungere.
        login_headers = {
            **headers,
            "X-SFDC-Page-Scope-Id": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": self._origin,
            "Referer": referer,
        }

        async with self._session.post(
            f"{self._su_host(AURA_ENDPOINT)}?r=2&other.PED_Login.loginUser=1",
            data=data,
            headers=login_headers,
        ) as resp:
            raw_text = await _leggi_testo(resp)
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError as err:
                _LOGGER.error(
                    "Risposta non-JSON da loginUser (status %s). Primi 500 "
                    "caratteri del corpo: %r",
                    resp.status,
                    raw_text[:500],
                )
                raise EdistribuzioneParsingError(
                    f"Risposta non-JSON da loginUser (status {resp.status}): "
                    f"{raw_text[:200]!r}"
                ) from err

        try:
            return_value = payload["actions"][0]["returnValue"]
        except (KeyError, IndexError) as err:
            raise EdistribuzioneParsingError(
                "Unexpected loginUser response shape"
            ) from err

        if not isinstance(return_value, str) or not return_value.startswith("OK:"):
            # Il limite di sessioni contemporanee arriva qui come messaggio
            # di errore della stessa action del login: trattarlo come
            # "credenziali non valide" (comportamento precedente) mandava
            # l'utente a ricontrollare email e password mentre il problema
            # era altrove - vedi _MARCATORI_TROPPE_SESSIONI.
            if isinstance(return_value, str) and _contiene(
                return_value, _MARCATORI_TROPPE_SESSIONI
            ):
                raise EdistribuzioneTroppeSessioni(return_value)
            raise EdistribuzioneInvalidCredentials(str(return_value))

        frontdoor_url = self._su_host(return_value[len("OK:") :])

        # Following this establishes the `sid` session cookie e atterra su
        # una pagina-ponte che fa un redirect via JAVASCRIPT
        # (window.location.replace(...)) verso il vero form OTP - un
        # browser lo segue automaticamente, noi no. Confermato analizzando
        # l'ordine cronologico reale di una HAR il 20/08/2026: la pagina di
        # frontdoor.jsp NON e' mai il form OTP, e' solo un ponte.
        resp = await _get_following_redirects(
            self._session, frontdoor_url, headers=headers, riscrivi=self._su_host
        )
        bridge_html = await resp.text()
        resp.close()
        _verifica_non_bloccato(bridge_html)

        otp_form_url = self._su_host(self._extract_js_redirect_url(bridge_html))
        resp = await _get_following_redirects(
            self._session, otp_form_url, headers=headers, riscrivi=self._su_host
        )
        otp_page_html = await resp.text()
        resp.close()
        _verifica_non_bloccato(otp_page_html)

        self._parse_otp_page(otp_page_html)
        await self._async_trigger_otp_send()

    async def _async_trigger_otp_send(self) -> None:
        """Il form OTP non parte da solo al caricamento della pagina: serve
        un primo submit del bottone, SENZA codice, per farlo davvero
        inviare - confermato su una HAR reale il 20/08/2026, la cui
        risposta contiene testualmente "Abbiamo inviato un codice a 5
        cifre al tuo indirizzo email" (non solo SMS, come si poteva
        pensare dal solo nome del campo OTP_Input).
        """
        await self._async_post_form_otp(self._dati_form_otp())

    async def async_resend_otp(self) -> bool:
        """Fa rimandare un OTP nuovo DENTRO la sessione di login gia' avviata.

        Serve perche' un OTP generato altrove (app ufficiale o sito) appartiene
        a un'altra "interview" del login flow di Salesforce e non puo' essere
        convalidato qui: se il codice non arriva in Home Assistant, l'unico
        modo di ottenerne uno valido e' farlo rispedire da questa stessa
        sessione (issue #2, dove l'utente riusciva a generare OTP validi sul
        sito ma nessuno di quelli funzionava nell'integrazione).

        Ritorna True se il portale ha confermato l'invio.
        """
        if self._flow.view_state is None:
            raise EdistribuzioneAuthError(
                "async_begin_login() deve riuscire prima di async_resend_otp()"
            )
        await self._async_post_form_otp(self._dati_form_otp(richiedi_nuovo=True))
        return bool(self.otp_invio_confermato)

    def _dati_form_otp(
        self, *, otp_code: str | None = None, richiedi_nuovo: bool = False
    ) -> dict[str, str]:
        """Payload del form RichFaces del login flow, identico per i tre
        submit che facciamo (invio iniziale senza codice, reinvio, convalida
        del codice): cambiano solo il campo OTP e il flag di reinvio."""
        next_field = self._flow.next_field_name or "thePage:j_id2:i:f:pb:pbb:nextAjax"
        dati = {
            "AJAXREQUEST": "_viewRoot",
            "thePage:j_id2:i:f": "thePage:j_id2:i:f",
            "thePage:j_id2:i:f:pb:d:navigationType": "",
            "com.salesforce.visualforce.ViewState": self._flow.view_state,
            "com.salesforce.visualforce.ViewStateVersion": self._flow.view_state_version,
            "com.salesforce.visualforce.ViewStateMAC": self._flow.view_state_mac,
            "com.salesforce.visualforce.ViewStateCSRF": self._flow.view_state_csrf,
            next_field: next_field,
        }
        if otp_code is not None:
            dati[
                self._flow.otp_field_name
                or "thePage:j_id2:i:f:pb:d:element___input____OTP_Input"
            ] = otp_code
        if otp_code is not None or richiedi_nuovo:
            dati[
                self._flow.resend_field_name
                or "thePage:j_id2:i:f:pb:d:element___hidden____Richiedi_nuovo_OTP"
            ] = "true" if richiedi_nuovo else "false"
        if richiedi_nuovo and self._flow.resend_checkbox_field_name:
            # Un browser con la casella spuntata invia anche la checkbox
            # visibile, non solo il campo hidden che la specchia: la
            # riproduciamo per stare il piu' vicino possibile al submit reale.
            dati[self._flow.resend_checkbox_field_name] = "true"
        return dati

    async def _async_post_form_otp(self, data: dict[str, str]) -> None:
        """Submit del form OTP che NON porta un codice da convalidare (invio
        iniziale o reinvio): controlla che il portale abbia davvero spedito
        l'OTP e aggiorna ViewState/CSRF dalla risposta.

        I token nella risposta di QUESTA chiamata sono quelli da usare per
        l'invio effettivo del codice (async_submit_otp), non quelli della
        pagina di atterraggio: ruotano ad ogni submit del form.
        """
        headers = {
            "User-Agent": _MOBILE_USER_AGENT,
            "Faces-Request": "partial/ajax",
        }
        url = self._su_host(self._flow.form_action_url or LOGINFLOW_URL)
        async with self._session.post(url, data=data, headers=headers) as resp:
            body = await _leggi_testo(resp)

        if _contiene(body, _MARCATORI_TROPPE_SESSIONI):
            _salva_pagina_debug(body, "otp_send_debug.html")
            raise EdistribuzioneTroppeSessioni(body[:500])

        self.otp_invio_confermato = _contiene(body, _MARCATORI_OTP_INVIATO)
        if not self.otp_invio_confermato:
            # Non fatale di proposito: la pagina potrebbe confermare l'invio
            # con parole che non conosciamo ancora, e abortire qui romperebbe
            # login che funzionano. Ma va segnalato, perche' l'altra
            # possibilita' e' che il codice non sia MAI stato spedito - il
            # sintomo dell'issue #2, prima invisibile nei log.
            _LOGGER.error(
                "E-Distribuzione non ha confermato l'invio del codice OTP "
                "(nessuno dei messaggi attesi %r nella risposta, lunga %d "
                "caratteri). Se il codice non arriva ne' via email ne' via "
                "SMS, allega otp_send_debug.html alla segnalazione.",
                _MARCATORI_OTP_INVIATO,
                len(body),
            )
            _salva_pagina_debug(body, "otp_send_debug.html")

        self._parse_otp_page(body)

    # -- Step 2: OTP ----------------------------------------------------------

    async def async_submit_otp(self, otp_code: str) -> OAuthTokens:
        """Submit the OTP and complete the OAuth2 code exchange."""
        if self._flow.view_state is None:
            raise EdistribuzioneAuthError(
                "async_begin_login() must succeed before async_submit_otp()"
            )

        headers = {
            "User-Agent": _MOBILE_USER_AGENT,
            "Faces-Request": "partial/ajax",
        }
        data = self._dati_form_otp(otp_code=otp_code)

        url = self._su_host(self._flow.form_action_url or LOGINFLOW_URL)
        async with self._session.post(url, data=data, headers=headers) as resp:
            body = await _leggi_testo(resp)

        if _contiene(body, _MARCATORI_TROPPE_SESSIONI):
            _salva_pagina_debug(body, "otp_submit_response_debug.html")
            raise EdistribuzioneTroppeSessioni(body[:500])

        if "Codice OTP" in body and "errato" in body.lower():
            raise EdistribuzioneInvalidOtp(body[:500])

        # Pull the "Location" meta tag content directly.
        loc_match = re.search(r'name="Location"\s+content="([^"]+)"', body)
        if not loc_match:
            _log_parsing_failure_context(body, "meta Location dopo l'invio dell'OTP")
            _salva_pagina_debug(body, "otp_submit_response_debug.html")
            raise EdistribuzioneParsingError(
                "Redirect (meta Location) non trovato nella risposta dopo "
                f"l'invio dell'OTP (risposta lunga {len(body)} caratteri - "
                "vedi log per un'anteprima)"
            )
        next_url = unquote(loc_match.group(1))
        if next_url.startswith("/"):
            next_url = self._origin + next_url
        next_url = self._su_host(next_url)

        # This page normally triggers a JS redirect to eneldist://redirect?code=...
        # We can't follow a custom URL scheme with aiohttp, so scrape the code
        # and state straight out of the returned HTML/JS instead of navigating.
        async with self._session.get(next_url, headers=headers) as resp:
            consent_html = await _leggi_testo(resp)

        code_match = re.search(r"[?&]code=([^&'\"]+)", consent_html)
        state_match = re.search(r"[?&]state=([^&'\"]+)", consent_html)

        if not code_match:
            # Prima volta che questo account autorizza l'app: Salesforce
            # mostra una schermata di consenso esplicita ("Consentire
            # l'accesso?") invece di rimandare subito il codice. Un browser
            # la mostra all'utente, noi dobbiamo premere "Consenti" al posto
            # suo - segnalato nell'issue #2 e confermato sulla pagina reale
            # il 13/09/2026. Dalla seconda volta in poi il consenso resta
            # memorizzato lato Salesforce e questo ramo non viene piu'
            # eseguito.
            risposta_consenso = await self._async_approva_consenso(consent_html)
            if risposta_consenso is not None:
                consent_html = risposta_consenso
                code_match = re.search(r"[?&]code=([^&'\"]+)", consent_html)
                state_match = re.search(r"[?&]state=([^&'\"]+)", consent_html)

        if not code_match:
            _log_parsing_failure_context(consent_html, "authorization code sulla pagina di consenso")
            _salva_pagina_debug(consent_html, "consent_page_debug.html")
            raise EdistribuzioneParsingError(
                "Could not find authorization code in consent page response "
                f"(pagina lunga {len(consent_html)} caratteri - vedi log per un'anteprima)"
            )
        if state_match and unquote(state_match.group(1)) != self._oauth_state:
            # Il parametro 'state' e' la protezione CSRF standard di OAuth:
            # generato e mandato da noi in async_begin_login, dovrebbe
            # tornare identico. Prima non veniva mai confrontato (estratto
            # e scartato) - trovato in audit il 21/08/2026. Solo un
            # warning, non un errore bloccante: questo metodo e' parte del
            # flusso confermato funzionante con un login reale, e non ho
            # verificato dal vivo se il confronto regge sempre (es.
            # differenze di codifica) - un falso positivo qui romperebbe
            # un login altrimenti valido.
            _LOGGER.warning(
                "State OAuth nella risposta non corrisponde a quello inviato "
                "(atteso %r, ricevuto %r) - procedo comunque, ma segnalalo se "
                "il login inizia a fallire qui",
                self._oauth_state,
                state_match.group(1),
            )
        auth_code = unquote(code_match.group(1))

        return await self._async_exchange_code(auth_code)

    async def _async_approva_consenso(self, html: str) -> str | None:
        """Preme "Consenti" sulla schermata di consenso OAuth di Salesforce.

        Ritorna il testo con cui cercare il codice di autorizzazione: il
        Location della risposta se c'e' un redirect (tipicamente verso
        eneldist://redirect?code=..., che aiohttp non puo' seguire perche'
        non e' uno schema HTTP), altrimenti il corpo della risposta. Ritorna
        None se questa pagina non e' una schermata di consenso riconoscibile,
        cosi' il chiamante prosegue con il suo errore di parsing abituale.
        """
        form = self._estrai_form_consenso(html)
        if form is None:
            return None

        action_url, dati = form
        action_url = self._su_host(action_url)
        _LOGGER.info(
            "Schermata di consenso OAuth rilevata (prima autorizzazione di "
            "questo account): confermo con %r", dati.get("save")
        )
        headers = {
            "User-Agent": _MOBILE_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self._origin,
            "Referer": f"{self._origin}/PortaleClienti/",
        }
        # allow_redirects=False: il redirect punta allo schema custom dell'app
        # (eneldist://), che aiohttp non sa seguire - il codice sta nel
        # Location, che qui possiamo leggere direttamente.
        async with self._session.post(
            action_url, data=dati, headers=headers, allow_redirects=False
        ) as resp:
            location = resp.headers.get("Location")
            body = await _leggi_testo(resp)
        return location or body

    @staticmethod
    def _estrai_form_consenso(html: str) -> tuple[str, dict[str, str]] | None:
        """Trova il form della schermata di consenso e costruisce il payload
        da rimandare: tutti i campi hidden cosi' come sono, piu' il pulsante
        di approvazione.

        Ritorna (url_action, dati) oppure None se non c'e' un form con un
        pulsante di approvazione riconoscibile.
        """
        for form_match in re.finditer(
            r"<form[^>]*>.*?</form>", html, re.IGNORECASE | re.DOTALL
        ):
            blocco = form_match.group(0)
            approva: tuple[str, str] | None = None
            for input_match in re.finditer(r"<input[^>]*>", blocco, re.IGNORECASE):
                tag = input_match.group(0)
                if not re.search(r'type="submit"', tag, re.IGNORECASE):
                    continue
                nome = re.search(r'name="([^"]*)"', tag)
                valore = re.search(r'value="([^"]*)"', tag)
                if not nome or not valore:
                    continue
                etichetta = html_lib.unescape(valore.group(1))
                if etichetta.strip().lower() in _ETICHETTE_CONSENSO:
                    approva = (nome.group(1), etichetta)
                    break
            if approva is None:
                continue

            dati = {}
            for input_match in re.finditer(r"<input[^>]*>", blocco, re.IGNORECASE):
                tag = input_match.group(0)
                if not re.search(r'type="hidden"', tag, re.IGNORECASE):
                    continue
                nome = re.search(r'name="([^"]*)"', tag)
                if not nome:
                    continue
                valore = re.search(r'value="([^"]*)"', tag)
                # I valori nell'HTML sono escapati (&amp;, &#39;): vanno
                # riportati in chiaro, altrimenti il server riceve campi
                # diversi da quelli che ha generato (save_new_url e source
                # sono URL pieni di parametri).
                dati[nome.group(1)] = (
                    html_lib.unescape(valore.group(1)) if valore else ""
                )
            dati[approva[0]] = approva[1]

            action = re.search(r'<form[^>]*action="([^"]*)"', blocco, re.IGNORECASE)
            action_url = html_lib.unescape(action.group(1)) if action else ""
            if not action_url:
                return None
            if action_url.startswith("/"):
                # Metodo statico: costruisce un URL sull'host principale, che
                # il chiamante riporta sull'host attivo con _su_host.
                action_url = f"https://{SF_HOST_PRINCIPALE}" + action_url
            return action_url, dati

        return None

    # -- Token exchange / refresh ---------------------------------------------

    async def _async_exchange_code(self, code: str) -> OAuthTokens:
        data = {
            "code": code,
            "code_verifier": self._code_verifier,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "authorization_code",
        }
        async with self._session.post(self._su_host(OAUTH_TOKEN_URL), data=data) as resp:
            payload = json.loads(await _leggi_testo(resp))
        return self._tokens_from_payload(payload)

    async def async_refresh_access_token(self, refresh_token: str) -> OAuthTokens:
        """Get a fresh access_token. No Aura/OTP involved - this is the path
        the integration should use on every normal startup/renewal.

        Prova prima l'host principale (cosi' si torna al dominio ufficiale
        appena Enel toglie il blocco) e ripiega sul diretto se il principale
        risponde con la pagina antibot.
        """
        self._host = SF_HOST_PRINCIPALE
        try:
            return await self._async_refresh_su_host(refresh_token)
        except EdistribuzioneBloccoAntibot:
            _LOGGER.info(
                "Refresh del token bloccato dall'antibot su %s, uso %s",
                SF_HOST_PRINCIPALE,
                SF_HOST_DIRETTO,
            )
            self._host = SF_HOST_DIRETTO
            return await self._async_refresh_su_host(refresh_token)

    async def _async_refresh_su_host(self, refresh_token: str) -> OAuthTokens:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        }
        async with self._session.post(self._su_host(OAUTH_TOKEN_URL), data=data) as resp:
            # Testo letto sempre, anche con status 200: la pagina "Pardon Our
            # Interruption" arriva con 200 e va riconosciuta prima di provare
            # a leggerla come JSON.
            text = await _leggi_testo(resp)
            if resp.status != 200:
                raise EdistribuzioneAuthError(
                    f"refresh_token exchange failed ({resp.status}): {text[:300]}"
                )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as err:
            raise EdistribuzioneParsingError(
                f"Risposta non-JSON dal token endpoint: {text[:200]!r}"
            ) from err
        # Salesforce doesn't always return a new refresh_token on refresh -
        # keep the old one if a new one isn't present.
        payload.setdefault("refresh_token", refresh_token)
        return self._tokens_from_payload(payload)

    @staticmethod
    def _tokens_from_payload(payload: dict) -> OAuthTokens:
        try:
            return OAuthTokens(
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                instance_url=payload.get("instance_url", ""),
                id_token=payload.get("id_token"),
                raw=payload,
            )
        except KeyError as err:
            raise EdistribuzioneParsingError(
                f"Token endpoint response missing expected field: {err}"
            ) from err

    # -- HTML/JSON scraping helpers --------------------------------------------
    # These are the parts most likely to need fixing against a fresh HAR.

    @staticmethod
    def _extract_fwuid(html: str) -> str:
        # Forma diretta: "fwuid":"<value>" letterale in JSON/JS embedded.
        match = re.search(r'"fwuid"\s*:\s*"([^"]+)"', html)
        if match:
            return match.group(1)

        # Forma percent-encoded: confermato su cattura reale il 20/08/2026,
        # il blob di contesto Aura e' incorporato direttamente nel PATH
        # dell'URL di uno <script src="...">, non in una variabile JS:
        #   <script src="/PortaleClienti/s/sfsites/l/%7B%22mode%22...
        #     %22fwuid%22%3A%22<value>%22...%7D/app.js">
        # Deve essere cosi' perche' virgolette letterali dentro un
        # attributo src="..." romperebbero il parsing HTML.
        match = re.search(r"%22fwuid%22%3A%22([^%]+)%22", html)
        if match:
            return match.group(1)

        raise EdistribuzioneParsingError("fwuid not found on login page")

    @staticmethod
    def _extract_loaded(html: str) -> str:
        """Estrae il valore grezzo (JSON, come stringa) del campo 'loaded'
        dallo stesso blob di bootstrap da cui si estrae fwuid - es.
        '{"APPLICATION@markup://siteforce:loginApp2":"1628_TW-..."}'.

        Necessario perche' aura.context con 'loaded':{} (vuoto) viene
        rifiutato dal server con un AuraClientInputException generico
        ("Unexpected request input... must be in the expected format"),
        confermato confrontando con una richiesta loginUser reale riuscita
        il 20/08/2026: il valore reale di 'loaded' e' non vuoto e va
        riportato identico.

        Ritorna '{}' (stringa) se non trovato, cosi' il chiamante degrada
        al comportamento precedente invece di fallire qui - il fallimento
        vero arrivera' comunque dal server sulla loginUser, con l'errore
        gia' diagnosticato da li'.
        """
        match = re.search(r'"loaded"\s*:\s*(\{[^}]*\})', html)
        if match:
            return match.group(1)

        match = re.search(r"%22loaded%22%3A(%7B.*?%7D)", html)
        if match:
            return unquote(match.group(1))

        return "{}"

    @staticmethod
    def _extract_js_redirect_url(html: str) -> str:
        """La pagina di frontdoor.jsp e' un ponte che rimanda al vero form
        OTP con `window.location.replace('URL')` (JavaScript, non un
        redirect HTTP) - un browser lo segue da solo, noi dobbiamo
        estrarre l'URL a mano e farci una GET esplicita.

        L'URL dentro replace() e' gia' assoluto e correttamente
        percent-encoded (e' dentro una stringa JS, non un attributo HTML),
        quindi nessun problema di doppia codifica come altrove in questo
        file - va usato cosi' com'e'.
        """
        match = re.search(r"window\.location\.replace\('([^']+)'\)", html)
        if match:
            return match.group(1)

        # Fallback: alcune varianti di questa pagina Salesforce usano
        # window.location.href invece di .replace(...).
        match = re.search(r"window\.location\.href\s*=\s*'([^']+)'", html)
        if match:
            return match.group(1)

        _log_parsing_failure_context(html, "window.location.replace(...) su pagina frontdoor")
        raise EdistribuzioneParsingError(
            "Redirect JavaScript verso il form OTP non trovato sulla pagina "
            f"di frontdoor (pagina lunga {len(html)} caratteri - vedi log "
            "per un'anteprima)"
        )

    def _parse_otp_page(self, html: str) -> None:
        def find(field_name: str) -> str:
            # Cerca l'intero tag <input ...> che contiene questo attributo
            # 'name', poi 'value' AL SUO INTERNO - indipendente dall'ordine
            # in cui i due attributi compaiono nel tag (il regex precedente
            # richiedeva 'name' prima di 'value', che potrebbe non essere
            # sempre vero).
            tag_match = re.search(
                rf'<input[^>]*name="{re.escape(field_name)}"[^>]*>', html
            )
            if not tag_match:
                _log_parsing_failure_context(html, field_name)
                raise EdistribuzioneParsingError(
                    f"Campo '{field_name}' non trovato sulla pagina OTP "
                    f"(pagina lunga {len(html)} caratteri - vedi log per un'anteprima)"
                )
            value_match = re.search(r'value="([^"]*)"', tag_match.group(0))
            if not value_match:
                raise EdistribuzioneParsingError(
                    f"Campo '{field_name}' trovato ma senza attributo 'value' "
                    f"leggibile: {tag_match.group(0)!r}"
                )
            return value_match.group(1)

        self._flow.view_state = find("com.salesforce.visualforce.ViewState")
        self._flow.view_state_version = find("com.salesforce.visualforce.ViewStateVersion")
        self._flow.view_state_mac = find("com.salesforce.visualforce.ViewStateMAC")
        self._flow.view_state_csrf = find("com.salesforce.visualforce.ViewStateCSRF")
        # Field name prefixes are Salesforce-generated and may shift between
        # deploys (`thePage:j_id2:...`) - try to discover them dynamically
        # rather than trusting the hardcoded defaults used as a fallback above.
        otp_field = re.search(r'name="([^"]*OTP_Input[^"]*)"', html)
        if otp_field:
            self._flow.otp_field_name = otp_field.group(1)
        # La pagina ha DUE campi con "Richiedi_nuovo_OTP" nel nome: una
        # checkbox visibile (element___input____...) e un campo nascosto
        # che la specchia (element___hidden____...), aggiornato da un
        # onclick JS - e' quest'ultimo che va sottomesso. Un regex generico
        # trova per primo la checkbox (appare prima nell'HTML), causando
        # l'invio del campo sbagliato - confermato su una risposta reale
        # il 20/08/2026, dove il server trattava la sottomissione del
        # codice come un'ennesima richiesta di reinvio invece che una
        # convalida.
        resend_field = re.search(
            r'name="([^"]*element___hidden____Richiedi_nuovo_OTP[^"]*)"', html
        )
        if resend_field:
            self._flow.resend_field_name = resend_field.group(1)
        resend_checkbox = re.search(
            r'name="([^"]*element___input____Richiedi_nuovo_OTP[^"]*)"', html
        )
        if resend_checkbox:
            self._flow.resend_checkbox_field_name = resend_checkbox.group(1)
        next_field = re.search(r'name="([^"]*nextAjax[^"]*)"', html)
        if next_field:
            self._flow.next_field_name = next_field.group(1)

        form_action = re.search(r'<form[^>]+action="([^"]+)"', html)
        if form_action:
            action = form_action.group(1)
            self._flow.form_action_url = (
                self._su_host(action) if action.startswith("http") else f"{self._origin}{action}"
            )
