"""Elenco comuni ISTAT per popolare le select a cascata del config flow.

Fetch live ad ogni avvio del config flow dal permalink ufficiale ISTAT.
Se il fetch fallisce per qualsiasi motivo, si ricade sullo snapshot
bundlato in data/istat_comuni_snapshot.json.

Fonte: https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv
(permalink dichiarato dall'ISTAT immutabile ad ogni aggiornamento del file,
vedi https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/)

Prima si usava il mirror JSON github.com/matteocontrini/comuni-json,
abbandonato perche' fermo al 01/01/2020 e dichiarato "non ufficiale" dal
suo stesso autore - vedi la nota in istat_transform.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .istat_transform import ComuniTree, comuni_csv_to_tree

_LOGGER = logging.getLogger(__name__)

ISTAT_CSV_URL = (
    "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
)
# Il CSV ISTAT e' pubblicato in cp1252, non UTF-8 (e servito con
# Content-Type application/octet-stream, quindi va decodificato a mano).
ISTAT_CSV_ENCODING = "cp1252"
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
    async with session.get(ISTAT_CSV_URL, timeout=30) as resp:
        resp.raise_for_status()
        grezzo = await resp.read()

    return comuni_csv_to_tree(grezzo.decode(ISTAT_CSV_ENCODING))
