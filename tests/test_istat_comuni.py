"""istat_comuni: niente parsing o lettura di file sull'event loop."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.contatore_letture import istat_comuni


def _hass():
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *a: f(*a))
    return hass


async def test_il_parsing_del_csv_live_gira_in_un_executor(monkeypatch):
    csv = "Codice Regione;x\n".encode(istat_comuni.ISTAT_CSV_ENCODING)
    risposta = MagicMock()
    risposta.raise_for_status = MagicMock()
    risposta.read = AsyncMock(return_value=csv)
    contesto = MagicMock()
    contesto.__aenter__ = AsyncMock(return_value=risposta)
    contesto.__aexit__ = AsyncMock(return_value=False)
    sessione = MagicMock()
    sessione.get = MagicMock(return_value=contesto)
    monkeypatch.setattr(istat_comuni, "async_get_clientsession", lambda hass: sessione)
    monkeypatch.setattr(istat_comuni, "comuni_csv_to_tree", MagicMock(return_value={"ok": 1}))
    hass = _hass()

    assert await istat_comuni._async_fetch_live(hass) == {"ok": 1}

    chiamata = hass.async_add_executor_job.await_args
    assert chiamata.args[0] is istat_comuni.comuni_csv_to_tree
    assert chiamata.args[1] == csv.decode(istat_comuni.ISTAT_CSV_ENCODING)


async def test_il_fallback_sullo_snapshot_gira_in_un_executor(monkeypatch):
    monkeypatch.setattr(istat_comuni, "_async_fetch_live", AsyncMock(side_effect=OSError("rete")))
    hass = _hass()

    albero = await istat_comuni.async_get_comuni_tree(hass)

    assert hass.async_add_executor_job.await_args.args[0] is istat_comuni._load_snapshot
    assert albero

