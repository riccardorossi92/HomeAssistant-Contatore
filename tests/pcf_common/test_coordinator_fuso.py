"""Il coordinator deve ragionare sul fuso configurato in Home Assistant,
non su quello di sistema.

Regressione per il passaggio da date.today() a dt_util.now().date(): vicino
a mezzanotte, per un utente in un fuso diverso da UTC, "oggi" e quindi il
giorno atteso venivano calcolati sul fuso sbagliato.
"""
from __future__ import annotations

from datetime import date

import pytest
from freezegun import freeze_time

from custom_components.contatore_letture.distributors.pcf_common.const import (
    CONF_DATA_INSTALLAZIONE,
)

pytestmark = pytest.mark.usefixtures("pcf_io")


async def test_giorno_atteso_segue_il_fuso_di_home_assistant(
    hass, make_pcf_coordinator, pcf_io
):
    """Ora locale gia' oltre la mezzanotte mentre a UTC e' ancora il giorno
    prima: il coordinator deve usare la data locale."""
    await hass.config.async_set_time_zone("Europe/Rome")

    # 23:30 UTC del 15 = 01:30 del 16 a Roma (UTC+2 con l'ora legale).
    pcf_io.parse_zip.return_value = {}
    coordinator = make_pcf_coordinator()  # primo avvio: nessun vincolo orario

    with freeze_time("2026-09-15 23:30:00"):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    # Data di installazione registrata = data LOCALE, non quella UTC.
    assert coordinator._entry.data.get(CONF_DATA_INSTALLAZIONE) == "2026-09-16"
    # atteso = oggi_locale - 1 giorno.
    da, a = coordinator.api.request_export.await_args.args[:2]
    assert (da, a) == (date(2026, 9, 15), date(2026, 9, 15))
