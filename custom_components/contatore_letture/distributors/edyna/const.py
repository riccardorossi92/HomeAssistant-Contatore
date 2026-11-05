"""Costanti del protocollo Edyna (portaledistributore.edyna.net, Alto Adige).

Portale Terranova PUF (ASP.NET WebForms, nessun JSON): login e curve orarie
verificati su due catture reali del 05/11/2026 - il login da un account di
prova senza POD, le curve da un account con un POD attivo. Quello che NON è
stato catturato (la catena di postback tra Utenze e la curva) è ricostruito
dagli identificativi dei controlli: vedi documentation/protocols/edyna-protocol.md,
sezione "Cosa manca".

DOMAIN NON sta qui: è unificato a livello di contatore_letture (vedi
il const.py principale), stesso principio degli altri distributori.
"""
from __future__ import annotations

import random
from collections.abc import Mapping
from datetime import time
from typing import Any

# --- Portale ------------------------------------------------------------------
PORTALE_URL = "https://portaledistributore.edyna.net"
# Il token "dD4" è fisso per l'istanza (stesso valore in tutte le catture e nei
# path del tema), non una sessione.
BASE_PAGINE = f"{PORTALE_URL}/EIPPUF/EIPPUF/it-IT/dD4/Page"
SINGLE_URL = f"{BASE_PAGINE}/Single.tws"
SINGLE_PATH = "/EIPPUF/EIPPUF/it-IT/dD4/Page/Single.tws"
LOGIN_URL = f"{BASE_PAGINE}/Login.tws"

DISPLAY_NAME = "Edyna"
# Confermata via query ARERA live (comuni di Bolzano e Salorno, 05/11/2026).
PIVA = "02689370217"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
# POD scoperti dall'account durante il config flow (griglia Utenze).
CONF_PODS = "pods"

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9",
}

# --- Nomi dei controlli (stessi nei path dei postback e negli id HTML) -------------
CAMPO_UTENTE = "ctl00$ctl00$body$body$cLogin$txtUser"
CAMPO_PASSWORD = "ctl00$ctl00$body$body$cLogin$txtPassword"
CAMPO_PULSANTE_LOGIN = "ctl00$ctl00$body$body$cLogin$btnLogin"
SCRIPT_MANAGER = "ctl00$ctl00$SManager"
SCRIPT_MANAGER_LOGIN = (
    "ctl00$ctl00$body$body$cLogin$upLogin|ctl00$ctl00$body$body$cLogin$btnLogin"
)
SECOND_POSTBACK = "body_ctl00_SecondPostback"

# Voce di menu di primo livello e ruolo/servizio della griglia Utenze.
MENU_UTENZE = "Utenze"
SERVIZIO_ELETTRICO = "Energia Elettrica"
# Unico ruolo importabile: Immissione/Produzione sarebbero energia prodotta.
RUOLO_PRELIEVO = "Prelievo"

# Suffissi degli id (gli id completi hanno il prefisso body_ctl00_ctl00_tcListUtenze_).
ID_GRIGLIA_UTENZE = "gvUtenze"
ID_CAMPO_RICERCA_UTENZE = "cUFListUtenze_txtCodute"
ID_ANNO = "TCurve_cCurve_ddlAnno"
ID_TABELLA_MESI = "TCurve_cCurve_gvCurveAttiva"
ID_GRIGLIA_MESI = "gvCurveAttiva"
ID_GRIGLIA_ORARIA = "gvCurveOrarieEE"
ID_TAB_CURVA_ORARIA = "__tab_body_ctl00_ctl00_tcListUtenze_TCurveOrarie"

# Ore per riga: 24 + una colonna extra per il giorno di fine ora legale.
COLONNE_ORARIE = 25

# --- Pianificazione ------------------------------------------------------------------
# Il portale è un'app WebForms con login e 5-8 postback per leggere un mese:
# una volta al giorno è abbastanza. Il ciclo di polling gira ogni ora ma tocca
# il portale solo dopo l'orario scelto, e al massimo una volta al giorno.
DEFAULT_UPDATE_INTERVAL_MINUTES = 60

CONF_DATA_INSTALLAZIONE = "data_installazione"
# Orario "HH:MM" (locale) da cui interrogare il portale, nelle opzioni
# della entry - stesso nome e formato degli altri distributori.
CONF_ORARIO_RICHIESTA = "orario_richiesta"

# Nella cattura del 05/11/2026 l'ultimo giorno disponibile era il 25/10: oltre
# 10 giorni di ritardo, di cui non si conosce la regola (cadenza fissa? per
# POD?). Per questo non si aspetta un "giorno atteso" con una coda di retry
# come SET/Ireti: ogni richiesta rilegge il mese dell'ultimo dato importato (e
# il successivo) e importa tutto quello che c'è di nuovo.
GIORNI_DI_RIPASSO = 7
# Mesi letti al primo avvio, e massimo di mesi per ciclo quando si è
# rimasti indietro. Lo storico più vecchio si recupera con recupera_storico.
MESI_PRIMO_AVVIO = 2
MAX_MESI_PER_CICLO = 3

# Limite di cortesia per recupera_storico: una richiesta per mese (ma ogni mese
# costa diversi postback), più un cambio anno ogni volta.
MAX_MESI_RECUPERO_STORICO = 36

ORARIO_CASUALE_DA_MINUTI = 6 * 60
ORARIO_CASUALE_A_MINUTI = 22 * 60


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
