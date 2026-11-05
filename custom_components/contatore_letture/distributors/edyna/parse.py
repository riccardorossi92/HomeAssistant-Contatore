"""Lettura dell'HTML del portale Edyna (ASP.NET WebForms, nessun JSON).

Funzioni pure, senza rete né Home Assistant, così si provano su HTML di
esempio. Il markup è quello delle catture reali del 05/11/2026 (vedi
documentation/protocols/edyna-protocol.md); un parser che non trova quello che
si aspetta solleva EdynaParseError invece di restituire dati parziali.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

_RE_DOPOSTBACK = re.compile(r"__doPostBack\('([^']*)'")
_RE_DATA = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
_RE_MESE_CURVA = re.compile(r"Consumi del mese:\s*(\d{1,2})/(\d{4})")

# Tipi di campo che il browser NON invia in un form (pulsanti e file): il
# pulsante premuto, se c'è, lo indica __EVENTTARGET.
_INPUT_NON_INVIATI = {"submit", "button", "image", "reset", "file"}


class EdynaParseError(Exception):
    """La pagina non ha la struttura attesa."""


@dataclass(frozen=True)
class Fornitura:
    """Una riga della griglia Utenze."""

    pod: str
    servizio: str
    ruolo: str
    stato: str
    # __EVENTTARGET del pulsante "Curve" della riga.
    target_curve: str


def parse_html(testo: str) -> BeautifulSoup:
    return BeautifulSoup(testo, "html.parser")


# --- Form ASP.NET ---------------------------------------------------------------------


def campi_form(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """I campi che un browser invierebbe inviando il form della pagina:
    nascosti (viewstate, ClientState...), testo, select col valore
    selezionato, checkbox/radio spuntati. Niente pulsanti."""
    form = soup.find("form", id="fMain") or soup.find("form")
    if form is None:
        raise EdynaParseError("Nessun form nella pagina")
    campi: list[tuple[str, str]] = []
    for el in form.find_all(["input", "select", "textarea"]):
        nome = el.get("name")
        if not nome or el.has_attr("disabled"):
            continue
        if el.name == "input":
            tipo = (el.get("type") or "text").lower()
            if tipo in _INPUT_NON_INVIATI:
                continue
            if tipo in ("checkbox", "radio"):
                if el.has_attr("checked"):
                    campi.append((nome, el.get("value", "on")))
                continue
            campi.append((nome, el.get("value", "")))
        elif el.name == "select":
            opzioni = el.find_all("option")
            scelte = [o for o in opzioni if o.has_attr("selected")]
            if not scelte and opzioni and not el.has_attr("multiple"):
                scelte = [opzioni[0]]
            for o in scelte:
                campi.append((nome, o.get("value", o.get_text(strip=True))))
        else:  # textarea
            campi.append((nome, el.get_text()))
    return campi


def dati_postback(
    campi: list[tuple[str, str]],
    target: str,
    argomento: str = "",
    sovrascrivi: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Corpo di un postback: i campi del form con __EVENTTARGET/__EVENTARGUMENT
    impostati e, per i nomi in 'sovrascrivi', il valore cambiato (aggiunto se
    il campo non c'era)."""
    valori = {"__EVENTTARGET": target, "__EVENTARGUMENT": argomento, **(sovrascrivi or {})}
    visti: set[str] = set()
    dati = []
    for nome, valore in campi:
        if nome in valori:
            if nome in visti:
                continue
            valore = valori[nome]
            visti.add(nome)
        dati.append((nome, valore))
    dati.extend((nome, valore) for nome, valore in valori.items() if nome not in visti)
    return dati


def target_da_href(href: str | None) -> str | None:
    """'javascript:__doPostBack('a$b','')' -> 'a$b'."""
    if not href:
        return None
    m = _RE_DOPOSTBACK.search(href)
    return m.group(1) if m else None


def target_per_id(soup: BeautifulSoup, suffisso_id: str) -> str | None:
    """__EVENTTARGET del link (<a>) il cui id finisce con 'suffisso_id'."""
    for a in soup.find_all("a", id=True):
        if a["id"].endswith(suffisso_id):
            return target_da_href(a.get("href"))
    return None


def target_menu(soup: BeautifulSoup, etichetta: str) -> str | None:
    """__EVENTTARGET della voce di menu di primo livello con quel testo."""
    for a in soup.find_all("a", href=True):
        if "lnkLevelMenu" in a["href"] and a.get_text(strip=True) == etichetta:
            return target_da_href(a["href"])
    return None


def e_pagina_di_login(soup: BeautifulSoup) -> bool:
    """Vero se la pagina è il modulo di login (sessione scaduta o rifiutata)."""
    return soup.find("input", attrs={"name": re.compile(r"cLogin\$txtUser$")}) is not None


def ha_elemento(soup: BeautifulSoup, suffisso_id: str) -> bool:
    return any(el["id"].endswith(suffisso_id) for el in soup.find_all(id=True))


# --- Risposta "delta" del login (UpdatePanel) ------------------------------------------


def redirect_dal_delta(testo: str) -> str | None:
    """Destinazione del 'pageRedirect' in una risposta UpdatePanel
    ('len|tipo|id|contenuto|' ripetuto), o None se non c'è."""
    pos = 0
    while pos < len(testo):
        fine_len = testo.find("|", pos)
        if fine_len < 0:
            return None
        try:
            lunghezza = int(testo[pos:fine_len])
        except ValueError:
            return None
        fine_tipo = testo.find("|", fine_len + 1)
        fine_id = testo.find("|", fine_tipo + 1)
        if fine_tipo < 0 or fine_id < 0:
            return None
        tipo = testo[fine_len + 1:fine_tipo]
        inizio = fine_id + 1
        contenuto = testo[inizio:inizio + lunghezza]
        if tipo == "pageRedirect":
            return unquote(contenuto)
        pos = inizio + lunghezza + 1
    return None


# --- Griglia Utenze ---------------------------------------------------------------------


def _testo_cella(cella: Tag) -> str:
    return re.sub(r"\s+", " ", cella.get_text(" ", strip=True)).strip()


def _tabella(soup: BeautifulSoup, suffisso_id: str) -> Tag | None:
    for t in soup.find_all("table", id=True):
        if t["id"].endswith(suffisso_id):
            return t
    return None


def parse_utenze(soup: BeautifulSoup) -> list[Fornitura]:
    """Le righe della griglia Utenze. Lista vuota se la griglia non c'è o è
    vuota ('Nessun risultato'): un account senza POD."""
    tabella = _tabella(soup, "gvUtenze")
    if tabella is None:
        return []
    righe = tabella.find_all("tr")
    if not righe:
        return []
    intestazioni = [_testo_cella(th) for th in righe[0].find_all("th")]
    try:
        i_servizio = intestazioni.index("Servizio")
        i_pod = intestazioni.index("Codice utenza")
        i_ruolo = intestazioni.index("Ruolo")
    except ValueError as err:
        raise EdynaParseError(f"Intestazioni della griglia Utenze inattese: {intestazioni}") from err
    i_stato = intestazioni.index("Stato") if "Stato" in intestazioni else None

    forniture = []
    for riga in righe[1:]:
        celle = riga.find_all("td")
        if len(celle) <= max(i_servizio, i_pod, i_ruolo):
            continue  # riga di paginazione o "Nessun risultato"
        target = None
        for a in riga.find_all("a", id=True):
            if re.search(r"_btnCurve_\d+$", a["id"]):
                target = target_da_href(a.get("href"))
        pod = _testo_cella(celle[i_pod])
        if not pod or target is None:
            continue
        forniture.append(
            Fornitura(
                pod=pod,
                servizio=_testo_cella(celle[i_servizio]),
                ruolo=_testo_cella(celle[i_ruolo]),
                stato=_testo_cella(celle[i_stato]) if i_stato is not None else "",
                target_curve=target,
            )
        )
    return forniture


# --- Tab Curve (mesi) ---------------------------------------------------------------------


def _select_per_suffisso(soup: BeautifulSoup, suffisso_id: str) -> Tag | None:
    for s in soup.find_all("select", id=True):
        if s["id"].endswith(suffisso_id):
            return s
    return None


def anno_selezionato(soup: BeautifulSoup) -> int | None:
    select = _select_per_suffisso(soup, "TCurve_cCurve_ddlAnno")
    if select is None:
        return None
    scelta = select.find("option", selected=True) or select.find("option")
    try:
        return int(scelta["value"]) if scelta is not None else None
    except (KeyError, ValueError):
        return None


def anni_disponibili(soup: BeautifulSoup) -> list[int]:
    select = _select_per_suffisso(soup, "TCurve_cCurve_ddlAnno")
    if select is None:
        return []
    anni = []
    for o in select.find_all("option"):
        try:
            anni.append(int(o["value"]))
        except (KeyError, ValueError):
            continue
    return anni


def nome_campo_anno(soup: BeautifulSoup) -> str | None:
    select = _select_per_suffisso(soup, "TCurve_cCurve_ddlAnno")
    return select.get("name") if select is not None else None


def target_mese(soup: BeautifulSoup, mese: int) -> str | None:
    """__EVENTTARGET del link del mese (1-12) nella tabella dell'Energia
    Attiva; None se il mese non è un link (nessun dato)."""
    return target_per_id(soup, f"gvCurveAttiva_btnCurve{mese}_0")


def kwh_mese(soup: BeautifulSoup, mese: int) -> float | None:
    """Consumo (kWh) mostrato nel link del mese; None se la cella è vuota
    (mese senza dati) o il link non c'è."""
    for a in soup.find_all("a", id=True):
        if a["id"].endswith(f"gvCurveAttiva_btnCurve{mese}_0"):
            return numero_italiano(a.get_text(" ", strip=True))
    return None


def tabella_mesi_vuota(soup: BeautifulSoup) -> bool:
    """True se la tabella dei mesi non ha nessun mese con un valore (il
    portale mostra "Nessun risultato"). Succede alla prima richiesta di
    Curve o di un anno nuovo, e per un anno davvero senza dati."""
    return all(kwh_mese(soup, mese) is None for mese in range(1, 13))


# --- Tab Curve orarie ---------------------------------------------------------------------


def mese_della_curva(soup: BeautifulSoup) -> tuple[int, int] | None:
    """(anno, mese) del titolo 'Consumi del mese: MM/AAAA'."""
    m = _RE_MESE_CURVA.search(soup.get_text(" ", strip=True))
    return (int(m.group(2)), int(m.group(1))) if m else None


def pod_della_curva(soup: BeautifulSoup) -> str | None:
    """POD della linguetta 'Energia Attiva: <POD>' del tab Curve orarie."""
    for el in soup.find_all(id=True):
        if el["id"].startswith("__tab_") and el["id"].endswith("tcListUtenze_TCurveOrarie"):
            testo = el.get_text(" ", strip=True)
            return testo.split(":", 1)[1].strip() if ":" in testo else testo
    return None


def numero_italiano(testo: str) -> float | None:
    """'0,176' -> 0.176; '1.234,5' -> 1234.5; vuoto o illeggibile -> None."""
    pulito = testo.strip().replace("\xa0", "").replace(".", "").replace(",", ".")
    if not pulito:
        return None
    try:
        return float(pulito)
    except ValueError:
        return None


def parse_curva_oraria(soup: BeautifulSoup) -> dict[date, list[float | None]]:
    """{giorno: [kWh ora 1 ... ora 25]} dalla griglia 'Dati di misura orari'.
    Una cella non numerica diventa None (il chiamante scarta il giorno).
    Solleva EdynaParseError se la griglia manca."""
    tabella = _tabella(soup, "gvCurveOrarieEE")
    if tabella is None:
        raise EdynaParseError("Griglia delle curve orarie non trovata")
    giorni: dict[date, list[float | None]] = {}
    for riga in tabella.find_all("tr"):
        celle = riga.find_all("td")
        if not celle:
            continue  # intestazione
        m = _RE_DATA.match(_testo_cella(celle[0]))
        if m is None:
            continue
        try:
            giorno = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        giorni[giorno] = [numero_italiano(_testo_cella(c)) for c in celle[1:]]
    return giorni
