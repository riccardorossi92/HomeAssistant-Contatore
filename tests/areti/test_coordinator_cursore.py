"""Avanzamento del cursore mensile dell'import automatico Areti.

Areti completa un mese chiuso progressivamente (osservato l'11/10/2026:
il 10/10 settembre arrivava solo fino al 20, il giorno dopo era
completo): il cursore deve passare al mese successivo solo quando la
curva copre tutto il mese, altrimenti i giorni pubblicati dopo resterebbero
un buco mai ricoperto.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.areti import create_coordinator
from custom_components.contatore_letture.distributors.areti.const import (
    CONF_MESE_DA_IMPORTARE,
    CONF_PODS,
)
from custom_components.contatore_letture.distributors.areti.coordinator import (
    _curva_copre_il_mese,
    _mese_da_considerare_chiuso,
    _ultimo_giorno_del_mese,
)

POD = "IT001E90000001"
_MODULO = "custom_components.contatore_letture.distributors.areti.coordinator"


def _curva(primo: date, ultimo: date) -> list[dict]:
    """Curva a 15 minuti da 'primo' a 'ultimo' (inclusi), 0.01 kWh a campione."""
    curva = []
    giorno = primo
    while giorno <= ultimo:
        for quarto in range(96):
            curva.append({
                "Value": "0.010",
                "Ora": f"{quarto // 4:02d}:{quarto % 4 * 15:02d}:00",
                "Data": giorno.isoformat(),
            })
        giorno += timedelta(days=1)
    return curva


def test_ultimo_giorno_del_mese():
    assert _ultimo_giorno_del_mese("092026") == date(2026, 9, 30)
    assert _ultimo_giorno_del_mese("022028") == date(2028, 2, 29)
    assert _ultimo_giorno_del_mese("122026") == date(2026, 12, 31)


def test_curva_copre_il_mese():
    assert _curva_copre_il_mese(_curva(date(2026, 9, 1), date(2026, 9, 30)), "092026")
    assert not _curva_copre_il_mese(_curva(date(2026, 9, 1), date(2026, 9, 20)), "092026")
    # Ultimo giorno presente ma tagliato prima dell'ultimo quarto d'ora.
    tagliata = _curva(date(2026, 9, 1), date(2026, 9, 30))[:-4]
    assert not _curva_copre_il_mese(tagliata, "092026")
    assert not _curva_copre_il_mese([], "092026")


def test_mese_da_considerare_chiuso():
    # Settembre si aspetta per tutto ottobre, si smette dal 1° novembre.
    assert not _mese_da_considerare_chiuso("092026", date(2026, 10, 11))
    assert not _mese_da_considerare_chiuso("092026", date(2026, 10, 31))
    assert _mese_da_considerare_chiuso("092026", date(2026, 11, 1))
    # A cavallo d'anno.
    assert not _mese_da_considerare_chiuso("122026", date(2027, 1, 31))
    assert _mese_da_considerare_chiuso("122026", date(2027, 2, 1))


@pytest.fixture
async def coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "areti",
            CONF_EMAIL: "a@b.it",
            CONF_PASSWORD: "x",
            CONF_PODS: [POD],
            CONF_MESE_DA_IMPORTARE: {POD: "092026"},
        },
    )
    entry.add_to_hass(hass)
    coord = create_coordinator(hass, entry)
    coord._async_login = AsyncMock(return_value=Mock())
    coord._async_config_pod = AsyncMock(return_value=("BP1", "CF1"))
    with (
        patch(f"{_MODULO}.async_import_curva_mensile", AsyncMock(return_value=None)) as importa,
        patch(f"{_MODULO}.async_get_ultima_data_disponibile", AsyncMock(return_value=None)),
    ):
        coord.importa = importa
        yield coord


async def _ciclo_con_curva(coord, curva) -> dict:
    api = await coord._async_login()
    api.async_get_misurazioni = AsyncMock(return_value={"elementiCurve": curva})
    return await coord._async_update_data()


async def test_mese_parziale_importa_ma_non_avanza(hass, coordinator, freezer):
    freezer.move_to("2026-10-10 12:00:00")
    dati = await _ciclo_con_curva(coordinator, _curva(date(2026, 9, 1), date(2026, 9, 20)))

    coordinator.importa.assert_awaited_once()
    assert coordinator.entry.data[CONF_MESE_DA_IMPORTARE][POD] == "092026"
    assert dati["by_pod"][POD]["mese_da_importare"] == "092026"
    assert dati["by_pod"][POD]["ultimo_mese_importato"] == "092026"


async def test_mese_completo_avanza(hass, coordinator, freezer):
    freezer.move_to("2026-10-11 12:00:00")
    dati = await _ciclo_con_curva(coordinator, _curva(date(2026, 9, 1), date(2026, 9, 30)))

    assert coordinator.entry.data[CONF_MESE_DA_IMPORTARE][POD] == "102026"
    assert dati["by_pod"][POD]["mese_da_importare"] == "102026"
    assert dati["by_pod"][POD]["kwh_ultimo_mese_importato"] == pytest.approx(28.8)


async def test_mese_ancora_parziale_dopo_un_mese_avanza_comunque(
    hass, coordinator, freezer, caplog
):
    freezer.move_to("2026-11-01 12:00:00")
    await _ciclo_con_curva(coordinator, _curva(date(2026, 9, 1), date(2026, 9, 20)))

    assert coordinator.entry.data[CONF_MESE_DA_IMPORTARE][POD] == "102026"
    assert "ancora incompleto" in caplog.text


async def test_risposta_senza_curva_non_avanza(hass, coordinator, freezer):
    freezer.move_to("2026-10-05 12:00:00")
    await _ciclo_con_curva(coordinator, [])

    assert coordinator.entry.data[CONF_MESE_DA_IMPORTARE][POD] == "092026"
