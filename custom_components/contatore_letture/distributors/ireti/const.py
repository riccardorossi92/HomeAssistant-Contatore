"""Costanti del protocollo Ireti (smartpod.ireti.it, gruppo Iren).

Verificato su dati reali: login diretto (31/08/2026, cattura HAR) e
soprattutto l'endpoint di misura (17/09/2026, issue #6 - russomichele,
POD attivo). Dettagli completi e "perché" in
documentation/protocols/ireti-protocol.md.

DOMAIN NON sta qui: è unificato a livello di contatore_letture (vedi
il const.py principale), stesso principio di pcf_common/edistribuzione/areti.
"""
from __future__ import annotations

# --- Portale (Angular + Keycloak) ----------------------------------------
BASE_URL = "https://smartpod.ireti.it"
REALM = "IRENSmartPOD"
CLIENT_ID = "SmartPOD-Angular"  # client pubblico: nessun client_secret
TOKEN_URL = f"{BASE_URL}/auth/realms/{REALM}/protocol/openid-connect/token"

DISPLAY_NAME = "Ireti"
# Confermata via query ARERA live (comune di Torino, 18/09/2026): "IRETI
# S.P.A.", Id operatore 3045, gruppo IREN. E' la chiave che fa scattare
# il routing automatico in distributors/__init__.py (PIVA_TO_KEY) - un
# valore diverso (es. quello riportato sul sito istituzionale ireti.it,
# 02863660359: probabilmente la capogruppo o un'altra società del gruppo
# Iren, non l'operatore di distribuzione registrato presso ARERA) avrebbe
# fatto fallire silenziosamente il riconoscimento automatico del
# distributore per qualunque comune servito da Ireti.
PIVA = "01791490343"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
# Lista di codici POD (stringhe): scoperti dall'account durante il config
# flow (getallbyconsumerandcompany), non inseriti a mano - come
# edistribuzione, non come areti/pcf.
CONF_PODS = "pods"

# Headers "da browser": senza questi, /users/* e /readings/* vanno in
# TIMEOUT (non 403 - la connessione viene scartata, comportamento tipico
# di un WAF) - verificato il 31/08/2026. Vedi ireti-protocol.md.
HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": f"{BASE_URL}/prelievi",
    "Origin": BASE_URL,
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

# Il refresh_token dura solo 30 minuti (refresh_expires_in: 1800,
# verificato) - inutilizzabile con un coordinator a ciclo giornaliero/orario.
# Si rifà il password-grant (auth.py) a ogni ciclo, come areti (che non ha
# nemmeno un refresh_token) - non come edistribuzione, il cui refresh_token
# Salesforce e' invece durevole. Un login in più a ciclo è irrilevante a
# questa cadenza.
DEFAULT_UPDATE_INTERVAL_MINUTES = 24 * 60

# measures-loadprofiles accetta un range arbitrario e restituisce un
# loadProfiles[] con un elemento per ogni giorno disponibile in quel range
# (confermato: un mese intero in una sola chiamata, issue #6) - quindi,
# a differenza di edistribuzione (una chiamata = un giorno, serve una coda
# per tracciare cosa manca) qui basta richiedere ogni ciclo una FINESTRA
# SCORREVOLE che copra abbondantemente qualunque ritardo di pubblicazione
# reale (non ancora misurato): quello che è già disponibile arriva, quello
# che manca semplicemente non compare nella risposta e rientra al ciclo
# successivo senza bisogno di uno stato persistito. Vedi "Design del
# coordinator" in ireti-protocol.md.
FINESTRA_GIORNI_DEFAULT = 14

# Limite di cortesia per l'azione recupera_storico (auto-imposto, non un
# vincolo noto delle API Ireti - l'unico esempio reale è una richiesta di
# un mese): stesso ordine di grandezza di areti (~2 anni), qui espresso in
# giorni perché measures-loadprofiles lavora a range di date, non a mesi.
MAX_GIORNI_RECUPERO_STORICO = 731
