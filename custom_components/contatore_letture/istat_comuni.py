"""Elenco comuni ISTAT per popolare le select a cascata del config flow.

Fetch live ad ogni avvio del config flow da un mirror GitHub del dataset
ISTAT (JSON pulito, nessun problema di encoding come il CSV originale
dell'ISTAT). Se il fetch fallisce per qualsiasi motivo, si ricade
sullo snapshot bundlato in data/istat_comuni_snapshot.json.

Fonte live: https://github.com/matteocontrini/comuni-json
Fonte originale dei dati: ISTAT (https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/)

Lo snapshot bundlato è stato generato una tantum a partire dalla stessa
fonte il giorno della creazione di questo file. Rigenerarlo raramente
è sufficiente: cambia solo in caso di fusioni/istituzioni di nuovi comuni.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .istat_transform import ComuniTree, comuni_list_to_tree

_LOGGER = logging.getLogger(__name__)

COMUNI_JSON_URL = (
    "https://raw.githubusercontent.com/matteocontrini/comuni-json/master/comuni.json"
)
SNAPSHOT_PATH = Path(__file__).parent / "data" / "istat_comuni_snapshot.json"


async def async_get_comuni_tree(hass: HomeAssistant) -> ComuniTree:
    """Ritorna {regione: {provincia: {comune: {codice_regione, codice_provincia, codice_comune}}}}."""
    try:
        return await _async_fetch_live(hass)
    except Exception as exc:  # noqa: BLE001 - qualunque errore, usa il fallback
        _LOGGER.warning(
            "Impossibile scaricare l'elenco comuni aggiornato (%s), uso lo snapshot "
            "bundlato con l'integrazione. Se il problema persiste, apri una issue: "
            "potrebbe essere cambiato il formato della sorgente dati.",
            exc,
        )
        return _load_snapshot()


def _load_snapshot() -> ComuniTree:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


async def _async_fetch_live(hass: HomeAssistant) -> ComuniTree:
    session = async_get_clientsession(hass)
    async with session.get(COMUNI_JSON_URL, timeout=30) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)

    return comuni_list_to_tree(data)
