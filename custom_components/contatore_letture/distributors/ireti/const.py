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

# --- Import automatico curva giornaliera + coda di retry ----------------------
# Stesso meccanismo di edistribuzione (vedi distributors/edistribuzione/const.py
# per il ragionamento completo, duplicato qui invece che condiviso perché
# ogni distributore resta un pacchetto a sé). measures-loadprofiles accetta
# un range arbitrario e restituisce un loadProfiles[] con un elemento per
# ogni giorno disponibile in quel range (confermato: un mese intero in una
# sola chiamata, issue #6), quindi come edistribuzione (e NON come una
# prima versione di questo file, che usava una finestra fissa senza stato
# persistito): ogni ciclo si chiede in una sola richiesta l'intervallo dal
# più vecchio giorno ancora in coda fino al giorno atteso - i giorni
# ricevuti escono dalla coda, quelli mancanti ci entrano. Una finestra
# fissa "abbastanza larga" (l'approccio precedente) faceva perdere
# silenziosamente un giorno mai pubblicato una volta uscito dalla finestra,
# senza nessun avviso - vedi "Design del coordinator" in ireti-protocol.md.

# A differenza di edistribuzione (dati reali su quando il giorno prima
# diventa disponibile, vedi la nota in edistribuzione/const.py), per Ireti
# NON abbiamo ancora nessuna misura del ritardo di pubblicazione reale:
# 1 giorno è un punto di partenza ragionevole ("il giorno prima"), non un
# valore confermato. Il meccanismo di coda lo rende comunque robusto anche
# se sbagliato: se il ritardo vero fosse maggiore, il giorno finisce
# semplicemente in coda finché non arriva, entro ABBANDONO_CODA_DOPO_GIORNI.
# Vedi anche il punto 6 di scripts/raccogli_dati_ireti.py, pensato apposta
# per misurarlo su un account reale.
RITARDO_DATI_GIORNI = 1

CONF_GIORNI_DA_RIPROVARE = "giorni_da_riprovare"

# Nessun CONF_ORA_RICHIESTA/ORA_MINIMA_RICHIESTA qui (a differenza di
# edistribuzione): quelli sono calibrati su un orario di pubblicazione
# osservato ("i dati del giorno prima sono già disponibili alle 18:00");
# per Ireti non abbiamo ancora nessuna osservazione simile, quindi non
# c'è ancora una base per un orario di cortesia - si prova a ogni ciclo,
# senza aspettare una certa ora. Da aggiungere se/quando emerge un pattern.

# Un giorno resta in coda e viene riprovato ai cicli successivi, abbandonato
# dopo questo numero di giorni REALI dal primo inserimento (non dopo N
# tentativi). Stesso valore di edistribuzione: ~1 settimana copre eventuali
# ritardi di pubblicazione senza accanirsi su date che non arriveranno mai;
# chi le vuole comunque puo' richiederle a mano con
# contatore_letture.recupera_storico.
ABBANDONO_CODA_DOPO_GIORNI = 7

# Nel ciclo automatico questo limite non viene mai avvicinato (la coda si
# stabilizza sui giorni di margine concessi sopra): serve solo quando un
# import copre un periodo lungo e la risposta torna incompleta, accodando
# molti giorni in un colpo solo.
MAX_GIORNI_IN_CODA = 30

# Limite di cortesia per l'azione recupera_storico (auto-imposto, non un
# vincolo noto delle API Ireti - l'unico esempio reale è una richiesta di
# un mese): stesso ordine di grandezza di areti (~2 anni), qui espresso in
# giorni perché measures-loadprofiles lavora a range di date, non a mesi.
MAX_GIORNI_RECUPERO_STORICO = 731
