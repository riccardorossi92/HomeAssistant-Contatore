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

CONF_POD = "pod"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN = "access_token"

DISPLAY_NAME = "E-Distribuzione"
PIVA = "05779711000"  # confermata via scheda operatore ARERA (Id operatore 435, gruppo ENEL)
