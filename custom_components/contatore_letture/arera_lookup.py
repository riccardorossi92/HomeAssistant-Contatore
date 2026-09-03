"""Query live ad ARERA per determinare il distributore elettrico di un comune.

Nessuna sessione/token richiesti: e' una GET con query string sui codici
ISTAT di regione/provincia/comune. Verificato via cattura HAR il 19/08/2026
sulla pagina https://www.arera.it/area-operatori/ricerca-operatori

Se la struttura della pagina ARERA dovesse cambiare in futuro, questo modulo
sollevera' AreraLookupError e il config flow ricadra' sulla selezione manuale:
nessun utente resta bloccato, ma repository maintainer verra' segnalato
dai log warning generati dagli utenti colpiti.
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

ARERA_SEARCH_URL = "https://www.arera.it/area-operatori/ricerca-operatori"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Home Assistant contatore_letture integration; "
                  "+https://github.com/riccardorossi92/HomeAssistant-Contatore)",
    "Referer": ARERA_SEARCH_URL,
    "Accept-Language": "it-IT,it;q=0.9",
}


class AreraLookupError(Exception):
    """Errore di rete o di parsing nella query verso ARERA."""


async def async_query_distributore(
    hass: HomeAssistant,
    codice_regione: str,
    codice_provincia: str,
    codice_comune: str,
) -> list[dict[str, str]]:
    """Interroga ARERA per il comune indicato.

    Ritorna una lista di {"ragione_sociale": str, "piva": str}.
    Lista vuota = nessun distributore elettrico trovato per il comune
    (non e' un errore: la rilevazione potrebbe non coprire quel comune).
    """
    session = async_get_clientsession(hass)
    params = {
        "territorio_ck": "elettricita_distributori",
        "tiporicerca": "PER_TERRITORIO",
        "cercaPerComuneComboRegione": codice_regione,
        "Ricercaoperatori_Province": codice_provincia,
        "Ricercaoperatori_Comuni": codice_comune,
        "RicercaTerritorio": "Cerca",
    }

    try:
        async with session.get(
            ARERA_SEARCH_URL, params=params, headers=_HEADERS, timeout=20
        ) as resp:
            resp.raise_for_status()
            html = await resp.text()
    except Exception as exc:  # noqa: BLE001
        raise AreraLookupError(f"Errore di rete verso ARERA: {exc}") from exc

    # Il parsing BeautifulSoup e' sincrono e su una pagina ARERA piena puo'
    # non essere istantaneo: lo spostiamo fuori dall'event loop.
    return await hass.async_add_executor_job(_parse_result, html)


def _parse_result(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("li.ricercaOperatoreItem")
    if not items:
        return []

    result = []
    for item in items:
        h3 = item.select_one("h3")
        link = item.select_one("a.operatori-detail-link")
        if h3 is None or link is None:
            raise AreraLookupError(
                "Struttura pagina ARERA cambiata: elementi 'h3'/'a.operatori-detail-link' "
                "non trovati dentro 'li.ricercaOperatoreItem'"
            )
        href = link.get("href", "")
        if "OperatorePartitaIva=" not in href:
            raise AreraLookupError(
                "Struttura pagina ARERA cambiata: parametro 'OperatorePartitaIva' "
                "non trovato nel link dell'operatore"
            )
        piva = href.split("OperatorePartitaIva=")[-1].strip()
        result.append({"ragione_sociale": h3.get_text(strip=True), "piva": piva})

    return result
