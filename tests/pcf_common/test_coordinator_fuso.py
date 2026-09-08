"""Il coordinator deve ragionare sul fuso configurato in Home Assistant,
non su quello di sistema né su UTC.

Regressione: "mese corrente" (e quindi se un mese è già chiuso e
richiedibile) va calcolato con dt_util.now().date(), non con
date.today(). A cavallo della mezzanotte di fine mese, per un utente in un
fuso diverso da UTC, i due valori cadono in mesi diversi.
"""
from __future__ import annotations

from datetime import date

import pytest
from freezegun import freeze_time

from custom_components.contatore_letture.distributors.pcf_common.const import (
    CONF_MESE_DA_IMPORTARE,
)

from .conftest import POD_TEST

pytestmark = pytest.mark.usefixtures("pcf_io")


async def test_mese_chiuso_segue_il_fuso_di_home_assistant(
    hass, make_pcf_coordinator, pcf_io
):
    """23:30 UTC del 31/08 = 01:30 del 01/09 a Roma: localmente agosto è
    appena chiuso e il cursore su '2026-08' deve far partire la richiesta,
    anche se a UTC è ancora il 31 agosto."""
    await hass.config.async_set_time_zone("Europe/Rome")
    pcf_io.parse_zip.return_value = {}
    coordinator = make_pcf_coordinator(data={CONF_MESE_DA_IMPORTARE: {POD_TEST: "2026-08"}})

    with freeze_time("2026-08-31 23:30:00"):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.api.request_export.await_count == 1
    da, a = coordinator.api.request_export.await_args.args[:2]
    assert (da, a) == (date(2026, 8, 1), date(2026, 8, 31))


async def test_cursore_iniziale_usa_il_mese_locale(hass, make_pcf_coordinator):
    """Il cursore di un POD nuovo parte dal mese CORRENTE locale: a Roma è
    già settembre, quindi '2026-09' e non '2026-08'."""
    await hass.config.async_set_time_zone("Europe/Rome")
    coordinator = make_pcf_coordinator()

    with freeze_time("2026-08-31 23:30:00"):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator._entry.data[CONF_MESE_DA_IMPORTARE] == {POD_TEST: "2026-09"}
    assert coordinator.api.request_export.await_count == 0
