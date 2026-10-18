"""Constants for the e-Distribuzione integration within contatore_letture.

Reverse-engineered from the official e-Distribuzione iOS app (HAR capture,
2026-08-19). See the "Note di sicurezza" in the top-level README: the HAR
used to build this contained real credentials of a test account.

DOMAIN is NOT here: it's unified at contatore_letture level (see ...const).
"""
from __future__ import annotations

import random
from collections.abc import Mapping
from datetime import time
from typing import Any

# --- Salesforce Experience Cloud (login / OAuth2 + PKCE) ---------------------
# La stessa org/community Salesforce (PortaleClienti) risponde su due host:
# - private.e-distribuzione.it e' il dominio personalizzato del portale,
#   dietro Imperva (Incapsula). Da ottobre 2026 risponde ai client non-browser
#   con la pagina antibot (403 "Incapsula incident ID" o "Pardon Our
#   Interruption"), bloccando sia il login sia il refresh del token (issue #8).
# - edistribuzione.my.site.com e' il dominio canonico che Salesforce assegna
#   all'org: stessa pagina di login, stesso client OAuth, senza Imperva
#   davanti (verificato il 10/10/2026: 200 sulla pagina di login e risposta
#   JSON di Salesforce dal token endpoint). Trovato da @paki81 per
#   fabioscarparo/HomeAssistant-EDistribuzione.
# Il dominio personalizzato resta il PRINCIPALE e si passa al diretto solo
# quando il principale e' bloccato: cosi' si torna da soli al dominio
# ufficiale quando Enel toglie il blocco (vedi auth.EdistribuzioneAuthClient).
SF_HOST_PRINCIPALE = "private.e-distribuzione.it"
SF_HOST_DIRETTO = "edistribuzione.my.site.com"
SF_HOSTS = (SF_HOST_PRINCIPALE, SF_HOST_DIRETTO)

SF_BASE = f"https://{SF_HOST_PRINCIPALE}/PortaleClienti"

# Host Salesforce diretto dell'org (l'instance_url restituito dal login), usato
# per il refresh del token. Verificato il 12/10/2026: qui il refresh risponde
# con un JSON valido senza Imperva davanti, e l'access_token ottenuto e'
# accettato dalle API misure (getSupplies). Sullo stesso refresh,
# edistribuzione.my.site.com risponde invece "app_blocked: this app is blocked
# by Transaction Security" (policy configurata da E-Distribuzione) e
# private.e-distribuzione.it con la pagina antibot di Imperva.
SF_HOST_TOKEN = "edistribuzione.my.salesforce.com"
OAUTH_TOKEN_URL_DIRETTO = f"https://{SF_HOST_TOKEN}/services/oauth2/token"

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
# A DIFFERENZA di pcf_common, RITARDO_DATI_GIORNI qui NON è stato
# verificato empiricamente con la stessa sistematicità di Duereti
# (confermato lì su settimane di test reali). Per E-Distribuzione i dati
# reali raccolti finora (21/08/2026, su un POD di test) sono: a
# mezzanotte/01:00 il giorno appena finito non è ancora disponibile
# (404), quello di 2 giorni prima sì; alle 18:00 il giorno precedente
# risulta invece già disponibile. Coerente con RITARDO_DATI_GIORNI=1
# (un giorno intero, non frazioni) - non più solo un'ipotesi per
# analogia con Duereti/Unareti, ma nessuna delle due distributrici lo
# documenta esplicitamente, quindi resta un valore dedotto dall'uso, non
# garantito. Il meccanismo di coda lo rende comunque robusto anche se
# sbagliato: se il ritardo vero fosse maggiore, il giorno finisce
# semplicemente in coda finché non arriva, entro ABBANDONO_CODA_DOPO_GIORNI.
RITARDO_DATI_GIORNI = 1

CONF_DATA_INSTALLAZIONE = "data_installazione"
CONF_GIORNI_DA_RIPROVARE = "giorni_da_riprovare"
# Orario "HH:MM" (locale) da cui chiedere il giorno precedente, nelle
# opzioni della entry. CONF_ORA_RICHIESTA e' il formato precedente (solo
# l'ora, intero 0-23, impostato a mano dalle opzioni): ancora letto per le
# entry che l'hanno, e sostituito da CONF_ORARIO_RICHIESTA al primo
# salvataggio delle opzioni.
CONF_ORARIO_RICHIESTA = "orario_richiesta"
CONF_ORA_RICHIESTA = "ora_richiesta"

# Vedi la nota estesa in pcf_common/const.py: un giorno resta in coda e viene
# riprovato ai cicli successivi, e viene abbandonato dopo questo numero di
# giorni REALI dal primo inserimento (non dopo N tentativi: il ritmo dei
# tentativi dipende dal tipo di errore ed era imprevedibile come misura di
# tempo). ~1 settimana copre eventuali ritardi di pubblicazione senza
# accanirsi su date che non arriveranno mai; chi le volesse comunque può
# richiederle a mano con contatore_letture.recupera_storico.
ABBANDONO_CODA_DOPO_GIORNI = 7

# Nel ciclo automatico questo limite non viene mai avvicinato (la coda si
# stabilizza sui giorni di margine concessi sopra): serve quando un import
# copre un periodo lungo e la risposta torna incompleta, accodando molti
# giorni in un colpo solo.
MAX_GIORNI_IN_CODA = 30

# Orario di default scelto a caso, una volta per installazione, tra le 16:00
# e le 20:00 (minuti compresi): cosi' le installazioni non chiedono tutte
# i dati nello stesso momento. Il dato del giorno prima e' risultato
# disponibile gia' alle 18:00 (21/08/2026); le 16 sono un anticipo non
# ancora verificato, coperto comunque dalla coda: se il giorno non c'e'
# ancora, viene riprovato ai cicli successivi. Un orario impostato a mano
# dalle opzioni sostituisce quello casuale.
ORARIO_CASUALE_DA_MINUTI = 16 * 60
ORARIO_CASUALE_A_MINUTI = 20 * 60


def orario_casuale() -> str:
    """Orario "HH:MM" casuale tra ORARIO_CASUALE_DA_MINUTI e
    ORARIO_CASUALE_A_MINUTI, estremi compresi."""
    minuti = random.randint(ORARIO_CASUALE_DA_MINUTI, ORARIO_CASUALE_A_MINUTI)
    return f"{minuti // 60:02d}:{minuti % 60:02d}"


def orario_da_opzioni(opzioni: Mapping[str, Any]) -> time | None:
    """Orario salvato nelle opzioni (CONF_ORARIO_RICHIESTA, o il vecchio
    CONF_ORA_RICHIESTA a sola ora); None se assente o non valido."""
    valore = opzioni.get(CONF_ORARIO_RICHIESTA)
    if valore is not None:
        try:
            return time.fromisoformat(str(valore)).replace(second=0, microsecond=0)
        except ValueError:
            return None
    valore = opzioni.get(CONF_ORA_RICHIESTA)
    if valore is None:
        return None
    try:
        ora = int(float(valore))
    except (TypeError, ValueError):
        return None
    return time(ora) if 0 <= ora <= 23 else None

# Limiti dell'azione recupera_storico, dalle Condizioni Generali dei servizi
# di E-Distribuzione (servizio "Curve di carico" dell'app e "Le mie misure"
# del portale): curva consultabile "per il periodo scelto (max 18 mesi;
# 6 mesi per volta)". async_get_daily_load_profile accetta l'intervallo in
# un'unica chiamata (confermato il 20/08/2026 su 181 giorni, 1 febbraio -
# 31 luglio), quindi una richiesta copre al massimo 6 mesi e la data di
# inizio non puo' essere anteriore a 18 mesi fa.
MAX_MESI_PER_RICHIESTA = 6
MAX_MESI_STORICO = 18

# Modalita' della config entry. "automatica" e' quella storica (login +
# OTP, download giornaliero via API); "manuale" non ha credenziali: l'utente
# scarica la curva dal portale e la carica dalle opzioni (csv_curve.py).
# Le entry create prima della v0.9.0 non hanno la chiave: sono automatiche.
CONF_MODALITA = "modalita"
MODALITA_AUTOMATICA = "automatica"
MODALITA_MANUALE = "manuale"
# Scelta del wizard, non un valore salvato: chiude il flow con i link ad
# ALTRE_INTEGRAZIONI senza creare nessuna entry.
MODALITA_ALTRE_INTEGRAZIONI = "altre_integrazioni"

# Integrazioni HACS di terzi per E-Distribuzione, proposte nel wizard come
# alternativa: dal 13/10/2026 offrono anche loro l'accesso dal browser al
# posto del login bloccato. Questo progetto non le sviluppa ne' le
# verifica. (nome, url)
ALTRE_INTEGRAZIONI = (
    (
        "fabioscarparo/HomeAssistant-EDistribuzione",
        "https://github.com/fabioscarparo/HomeAssistant-EDistribuzione",
    ),
    (
        "maurobraggio/HomeAssistant-EDistribuzione",
        "https://github.com/maurobraggio/HomeAssistant-EDistribuzione",
    ),
)
# Scelta del wizard, non un valore salvato: l'accesso dal browser crea una
# entry automatica come quella con password e OTP (stesso refresh_token,
# stesso download giornaliero), cambia solo come si ottiene il primo token.
MODALITA_BROWSER = "browser"

# Da ottobre 2026 il login automatico con password e OTP e' bloccato (issue
# #11): Imperva su private.e-distribuzione.it, "Errore Tecnico" su
# my.site.com. Nel wizard resta visibile ma non selezionabile; l'accesso
# dal browser (MODALITA_BROWSER) lo sostituisce. Rimettere True quando
# torna a funzionare.
SCARICAMENTO_AUTOMATICO_DISPONIBILE = False

# Il CSV del portale e' in ora italiana, qualunque sia il fuso di Home Assistant.
FUSO_PORTALE = "Europe/Rome"

# Pagina del portale da cui si esporta il CSV, mostrata nel form di caricamento.
URL_CURVE_DI_CARICO = "https://private.e-distribuzione.it/PortaleClienti/s/curvedicarico"
