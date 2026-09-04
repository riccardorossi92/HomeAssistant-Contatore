"""Costanti del protocollo Areti (Roma e Formello, gruppo ACEA).

Reverse-engineered da catture reali (HAR) del 04/09/2026, documentate per
esteso in documentation/areti-protocol.md - questo file riporta solo le
costanti, i "perché" stanno lì.

DOMAIN NON sta qui: è unificato a livello di contatore_letture (vedi
il const.py principale), stesso principio di pcf_common/edistribuzione.
"""
from __future__ import annotations

# --- Portale (Salesforce Experience Cloud, framework Aura) --------------------
BASE_URL = "https://areariservataclienti.areti.it"
PORTAL_URL = f"{BASE_URL}/portaleareti/s/"

LOGIN_URL = f"{BASE_URL}/portaleareti/AretiLoginURL"
AURA_URL = f"{BASE_URL}/portaleareti/s/sfsites/aura"
HOME_URL = f"{BASE_URL}/portaleareti/s/"

DISPLAY_NAME = "Areti"
PIVA = "05816611007"  # confermata via scheda operatore ARERA (Id operatore 338, gruppo Acea)

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
# Lista di codici POD (stringhe), come edistribuzione - non pcf_common: sono
# tutti sulla stessa utenza già autenticata, nessun dato fiscale da inserire
# (getConfigurations lo risolve da solo per ogni POD).
CONF_PODS = "pods"

# {pod: "MMYYYY"} - il mese più vecchio non ancora importato con successo
# per ciascun POD. Vedi coordinator.py e la sezione "Design del
# coordinator" in areti-protocol.md per il ragionamento completo: a
# differenza di pcf_common/edistribuzione (sempre "il giorno/mese
# precedente rispetto a oggi", ricalcolato ogni ciclo), qui è un cursore
# persistito che avanza solo quando trova dati - Areti pubblica a mese
# solare chiuso, non giorno per giorno, quindi non c'è un "ritardo in
# giorni" noto da cui dedurre la data giusta da chiedere ogni volta.
CONF_MESE_DA_IMPORTARE = "mese_da_importare"

# Un ciclo al volta: la granularità dei dati è mensile, controllare più
# spesso non avrebbe senso (scelta esplicita, non un default preso a caso -
# vedi "Design del coordinator" in areti-protocol.md). A differenza di
# pcf_common/edistribuzione non c'è un orario configurabile "ora_richiesta":
# non sappiamo a che ora del giorno Areti pubblica un mese appena chiuso
# (sappiamo solo che agosto era già disponibile il 4 settembre), quindi non
# c'è ancora una base per renderlo configurabile.
DEFAULT_UPDATE_INTERVAL_MINUTES = 24 * 60

# Componente energia richiesta per l'import automatico: "EA" = energia
# attiva ENTRANTE, cioè il prelievo/consumo dalla rete - quella che serve
# per la Energy Dashboard di un cliente normale (non un impianto di
# produzione, che vorrebbe "UA", energia attiva uscente - vedi "Cosa resta
# aperto" in areti-protocol.md, non ancora supportato).
COMPONENTE_ENERGIA_DEFAULT = "EA"

# Limite di cortesia per l'azione recupera_storico (auto-imposto, non un
# vincolo noto delle API Areti - non ne abbiamo mai richiesti più di uno
# alla volta finora): ~2 anni, ordine di grandezza più permissivo del
# limite di 6 mesi di pcf_common perché ogni richiesta qui costa un solo
# mese (una singola chiamata getMisurazioni), non un export pesante.
MAX_MESI_RECUPERO_STORICO = 24
