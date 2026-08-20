"""Constants for the e-Distribuzione integration within contatore_letture.

Reverse-engineered from the official e-Distribuzione iOS app (HAR capture,
2026-08-19). See the "Note di sicurezza" in the top-level README: the HAR
used to build this contained real credentials of a test account.

DOMAIN is NOT here: it's unified at contatore_letture level (see ...const).
"""
from __future__ import annotations

# --- Salesforce Experience Cloud (login / OAuth2 + PKCE) ---------------------
SF_BASE = "https://private.e-distribuzione.it/PortaleClienti"

OAUTH_AUTHORIZE_URL = f"{SF_BASE}/services/oauth2/authorize"
OAUTH_TOKEN_URL = f"{SF_BASE}/services/oauth2/token"
OAUTH_USERINFO_URL = f"{SF_BASE}/services/oauth2/userinfo"

LOGIN_PAGE_URL = f"{SF_BASE}/s/login/"
AURA_ENDPOINT = f"{SF_BASE}/s/sfsites/aura"
LOGINFLOW_URL = f"{SF_BASE}/loginflow/loginFlow.apexp"

# Client ID of the official e-Distribuzione Connected App (public, native-app
# style OAuth client - no client_secret is used, consistent with PKCE).
# Reusing Enel's own client_id is what makes headless login possible, but note
# this is a third-party client relying on Enel's infrastructure - treat it as
# fragile and be ready for it to be revoked/changed without notice.
OAUTH_CLIENT_ID = (
    "3MVG9Rd3qC6oMalUVUXDfNEIi52RXSZMsndjnAnbka4R4Amhq6QrTj2U2Zw0sjyqOlFViLC"
    ".cTpau8fPEBV0V"
)
OAUTH_REDIRECT_URI = "eneldist://redirect"
OAUTH_SCOPE = "web api openid id profile email address phone refresh_token offline_access"

# --- Mulesoft backend (actual data APIs) --------------------------------------
MISURE_BASE_URL = "https://xs-misura-p.de-c1.eu1.cloudhub.io/xs/misure"
APP_MNGMT_BASE_URL = "https://xs-app-api-mngmt.de-c1.eu1.cloudhub.io"

MISURE_READING_URL = f"{MISURE_BASE_URL}/reading"
MISURE_DAILY_LOAD_PROFILE_URL = f"{MISURE_BASE_URL}/querydailyloadprofile"
MISURE_MONTHLY_LOAD_PROFILE_URL = f"{MISURE_BASE_URL}/querymonthlyloadprofile"
MISURE_MONTHLY_TIME_OF_USE_URL = f"{MISURE_BASE_URL}/querymonthlytimeofuse"
MISURE_GET_SUPPLIES_URL = f"{MISURE_BASE_URL}/getSupplies"

# Method_User header values seen per endpoint - the backend uses this as an
# application-level permission/scope discriminator, distinct from the HTTP verb.
METHOD_USER_ELENCO_POD = "ELENCO_POD"
METHOD_USER_LETTURE = "LETTURE"
METHOD_USER_CURVA_GIORNO = "CURVE_DI_CARICO-GIORNO"
METHOD_USER_CURVA_MESE = "CURVE_DI_CARICO-MESE"
METHOD_USER_CURVA_PERIODO = "CURVE_DI_CARICO-PERIODO"

DEFAULT_UPDATE_INTERVAL_MINUTES = 60

CONF_PODS = "pods"  # lista di codici POD (stringhe), non dict come pcf_common: qui non serve un dato fiscale per POD, sono tutti sulla stessa utenza gia' autenticata
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN = "access_token"

DISPLAY_NAME = "E-Distribuzione"
PIVA = "05779711000"  # confermata via scheda operatore ARERA (Id operatore 435, gruppo ENEL)

# --- Import automatico curva giornaliera + coda di retry ----------------------
# Stesso meccanismo di pcf_common (vedi distributors/pcf_common/const.py per
# il ragionamento completo), duplicato qui invece che condiviso perché
# edistribuzione resta un pacchetto a sé - se in futuro il comportamento reale
# di E-Distribuzione divergesse da quello di Duereti/Unareti, non tocchi due
# distributori diversi per una modifica che vale solo per uno.
#
# A DIFFERENZA di pcf_common, RITARDO_DATI_GIORNI qui NON è stato verificato
# empiricamente: per Duereti è confermato su settimane di test reali, per
# E-Distribuzione al momento sappiamo solo che un giorno vecchio di 19 giorni
# funziona - non sappiamo quanto recente possa essere. Il valore sotto è
# un'ipotesi di partenza per analogia con Duereti/Unareti (stesso tipo di
# distributore, stesso ordine di grandezza plausibile), corretta di fatto
# dal meccanismo di coda: se il ritardo vero è maggiore, il giorno finisce
# semplicemente in coda finché non arriva, entro MAX_TENTATIVI_PER_GIORNO.
RITARDO_DATI_GIORNI = 1

CONF_DATA_INSTALLAZIONE = "data_installazione"
CONF_GIORNI_DA_RIPROVARE = "giorni_da_riprovare"
CONF_ORA_RICHIESTA = "ora_richiesta"

MAX_TENTATIVI_PER_GIORNO = 10
MAX_GIORNI_IN_CODA = 30

# Anche questo è un punto di partenza non confermato per E-Distribuzione
# (per Duereti è stato osservato che i dati del giorno prima spesso non ci
# sono ancora alle 10 del mattino ma ci sono la sera). Modificabile dalle
# opzioni dell'integrazione.
ORA_MINIMA_RICHIESTA = 19

# Limite di cortesia auto-imposto per l'azione recupera_storico (non è un
# vincolo noto delle API E-Distribuzione, a differenza del limite di 6 mesi
# per richiesta di Duereti/Unareti, che è documentato nel loro manuale PCF):
# async_get_daily_load_profile supporta un intervallo multi-giorno in
# un'unica chiamata (confermato il 20/08/2026 fino a 181 giorni), quindi
# non è più un limite anti-spam come nella prima versione - resta solo per
# evitare che un errore di battitura nella data richieda anni di dati per
# sbaglio in una singola risposta.
MAX_GIORNI_RECUPERO_STORICO = 190  # ~6 mesi, stesso ordine di grandezza di pcf_common
