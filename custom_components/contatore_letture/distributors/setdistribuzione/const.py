"""Costanti del protocollo SET Distribuzione (myset.setdistribuzione.it,
gruppo Dolomiti Energia).

Verificato su una cattura reale di un account con POD attivo (13/10/2026):
login Azure AD B2C, elenco forniture e curva oraria con quarti d'ora.
Dettagli completi e "perché" in
documentation/protocols/set-distribuzione-protocol.md.

DOMAIN NON sta qui: è unificato a livello di contatore_letture (vedi
il const.py principale), stesso principio degli altri distributori.
"""
from __future__ import annotations

import random
from collections.abc import Mapping
from datetime import time
from typing import Any

# --- Portale (SPA React + Azure AD B2C, config in window.privatearea) -------
PORTALE_URL = "https://myset.setdistribuzione.it"
API_BASE = "https://agw.setdistribuzione.it/api"

B2C_HOST = "https://sportelloclientib2cset.b2clogin.com"
B2C_TENANT = "sportelloclientib2cset.onmicrosoft.com"
B2C_POLICY = "B2C_1A_signin"
B2C_POLICY_BASE = f"{B2C_HOST}/{B2C_TENANT}/{B2C_POLICY}"
AUTHORIZE_URL = f"{B2C_POLICY_BASE}/oauth2/v2.0/authorize"
SELF_ASSERTED_URL = f"{B2C_POLICY_BASE}/SelfAsserted"
CONFIRMED_URL = f"{B2C_POLICY_BASE}/api/CombinedSigninAndSignup/confirmed"
TOKEN_URL = f"{B2C_POLICY_BASE}/oauth2/v2.0/token"

# Client pubblico della SPA (nessun client_secret, PKCE obbligatorio).
CLIENT_ID = "3cfe517e-7c9e-433e-a9dd-daf4cca499c9"
REDIRECT_URI = PORTALE_URL
SCOPE = f"https://{B2C_TENANT}/private/api/read openid profile offline_access"

DISPLAY_NAME = "SET Distribuzione"
# Confermata via query ARERA live (comune di Rovereto, 13/10/2026): è la
# chiave del riconoscimento automatico in distributors/__init__.py.
PIVA = "01932800228"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
# Lista di codici POD scoperti dall'account durante il config flow
# (utility/{codice fiscale}/active), non inseriti a mano.
CONF_PODS = "pods"

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9",
}

# Il token endpoint B2C rilascia i token di un client "Single-Page
# Application" solo a richieste cross-origin: senza Origin risponde
# AADSTS9002327. Stesso Origin anche sulle API, come fa il browser.
HEADERS_ORIGINE_PORTALE = {
    "Origin": PORTALE_URL,
    "Referer": f"{PORTALE_URL}/",
}

# access_token da 1 ora, refresh_token da 24 ore non rinnovabili oltre
# (limite B2C per i client SPA): conviene rifare il login completo quando
# serve, come Ireti, invece di conservare token. Il ciclo orario è solo un
# controllo locale: il login si fa dopo l'orario della richiesta e solo se
# c'è davvero un giorno da chiedere (vedi coordinator.py).
DEFAULT_UPDATE_INTERVAL_MINUTES = 60

# --- Import automatico curva giornaliera + coda di retry ----------------------
# Stesso meccanismo di edistribuzione/ireti, duplicato qui perché ogni
# distributore resta un pacchetto a sé. Differenza: la curva con i quarti
# d'ora si chiede UN GIORNO PER RICHIESTA (parametro 'day'), quindi ogni
# giorno in coda è una chiamata a sé.

# Nella cattura (13/10/2026 alle 22:23) il giorno precedente era già
# completo e quello in corso assente: "il giorno prima", come Ireti. L'ora
# esatta di pubblicazione non è nota; la coda copre comunque i ritardi.
RITARDO_DATI_GIORNI = 1

CONF_GIORNI_DA_RIPROVARE = "giorni_da_riprovare"
CONF_DATA_INSTALLAZIONE = "data_installazione"
# Orario "HH:MM" (locale) da cui chiedere il giorno precedente, nelle
# opzioni della entry - stesso nome e formato di edistribuzione.
CONF_ORARIO_RICHIESTA = "orario_richiesta"

# Orario scelto a caso, una volta per installazione, tra le 18:00 e le
# 22:00 (minuti compresi), come fa edistribuzione tra le 16 e le 20: così
# le installazioni non chiedono tutte i dati nello stesso momento. Nella
# cattura il giorno precedente era già completo alle 22:23; prima di
# allora non è verificato, ma un giorno non ancora pubblicato resta in
# coda e viene richiesto di nuovo al ciclo successivo.
ORARIO_CASUALE_DA_MINUTI = 18 * 60
ORARIO_CASUALE_A_MINUTI = 22 * 60

# Stessi valori di edistribuzione/ireti: ~1 settimana di tentativi, poi
# il giorno si recupera a mano con contatore_letture.recupera_storico.
ABBANDONO_CODA_DOPO_GIORNI = 7
MAX_GIORNI_IN_CODA = 30

# Limite di cortesia per recupera_storico: una richiesta per giorno, quindi
# un anno sono già ~366 chiamate in fila.
MAX_GIORNI_RECUPERO_STORICO = 366


def orario_casuale() -> str:
    """Orario "HH:MM" casuale tra ORARIO_CASUALE_DA_MINUTI e
    ORARIO_CASUALE_A_MINUTI, estremi compresi."""
    minuti = random.randint(ORARIO_CASUALE_DA_MINUTI, ORARIO_CASUALE_A_MINUTI)
    return f"{minuti // 60:02d}:{minuti % 60:02d}"


def orario_da_opzioni(opzioni: Mapping[str, Any]) -> time | None:
    """Orario salvato nelle opzioni (CONF_ORARIO_RICHIESTA); None se
    assente o non valido."""
    valore = opzioni.get(CONF_ORARIO_RICHIESTA)
    if valore is None:
        return None
    try:
        return time.fromisoformat(str(valore)).replace(second=0, microsecond=0)
    except ValueError:
        return None
