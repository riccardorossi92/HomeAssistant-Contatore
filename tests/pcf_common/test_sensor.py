"""Test dei sensori diagnostici PCF: valore e attributi in base a
coordinator.data, più il conteggio delle entità costruite.

Il coordinator è un finto SimpleNamespace: questi sensori non chiamano
metodi del coordinator, leggono solo i suoi attributi (data, token_ok,
pending_since, ...).
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.pcf_common import sensor as s

POD = "IT001E00000001"


def _entry(hass=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"pods": [{"pod": POD, "df": "DF"}]},
        title="Duereti (1 POD)",
    )
    if hass is not None:
        entry.add_to_hass(hass)
    return entry


def _coord(**dati):
    return SimpleNamespace(
        data=dati or None,
        token_ok=True,
        pending_since=None,
        pending_ticket=None,
    )


def test_ultimo_import_valore_e_disponibilita():
    coord = _coord(
        ultimo_aggiornamento="2026-09-01",
        stato="ok",
        pod_aggiornati=[POD],
        ultimo_errore=None,
    )
    sensore = s.PcfUltimoImportSensor(coord, _entry(), "Duereti")
    assert sensore.native_value == "2026-09-01"
    assert sensore.extra_state_attributes["stato"] == "ok"
    assert sensore.available is True

    coord.token_ok = False
    assert sensore.available is False


def test_ultimo_import_senza_dati_usa_il_ripristino():
    sensore = s.PcfUltimoImportSensor(_coord(), _entry(), "Duereti")
    assert sensore.native_value is None
    sensore._ripristinato = "2026-08-31"
    assert sensore.native_value == "2026-08-31"


def test_ultima_data_disponibile_per_pod():
    coord = _coord(ultime_date_per_pod={POD: "2026-09-02"})
    sensore = s.PcfUltimaDataDisponibileSensor(coord, _entry(), POD, "Duereti")
    assert sensore.native_value == date(2026, 9, 2)

    sensore_altro = s.PcfUltimaDataDisponibileSensor(coord, _entry(), "IT999", "Duereti")
    assert sensore_altro.native_value is None


def test_consumo_periodo_per_pod():
    coord = _coord(
        totale_kwh_periodo_per_pod={POD: 12.5},
        periodo_importato="2026-09-01 - 2026-09-02",
    )
    sensore = s.PcfConsumoPeriodoSensor(coord, _entry(), POD, "Duereti")
    assert sensore.native_value == 12.5
    assert sensore.extra_state_attributes["periodo"] == "2026-09-01 - 2026-09-02"


async def test_stato_attesa_conta_i_minuti():
    coord = _coord()
    sensore = s.PcfStatoAttesaSensor(coord, _entry(), "Duereti")

    await sensore.async_update()
    assert sensore.native_value == 0

    coord.pending_since = dt_util.utcnow() - timedelta(minutes=42)
    coord.pending_ticket = "TCK"
    await sensore.async_update()
    assert sensore.native_value == 42
    assert sensore.extra_state_attributes["in_attesa"] is True
    assert sensore.extra_state_attributes["ticket"] == "TCK"


def test_pod_configurati():
    pods = [{"pod": POD, "df": "DF"}, {"pod": "IT002", "df": "DF2"}]
    sensore = s.PcfPodConfiguratiSensor(_coord(), _entry(), pods, "Duereti")
    assert sensore.native_value == 2
    assert sensore.extra_state_attributes["pods"] == [POD, "IT002"]


async def test_build_pcf_entities_conta_account_piu_due_per_pod(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"pods": [{"pod": POD, "df": "DF"}, {"pod": "IT002", "df": "DF2"}]},
        title="Duereti (2 POD)",
    )
    entry.add_to_hass(hass)
    entità = s.build_pcf_entities(hass, _coord(), entry, "Duereti")
    # 3 sull'account + 2 per ciascuno dei 2 POD
    assert len(entità) == 3 + 2 * 2
    nomi = {type(e).__name__ for e in entità}
    assert "PcfPodConfiguratiSensor" in nomi
    assert "PcfUltimaDataDisponibileSensor" in nomi
