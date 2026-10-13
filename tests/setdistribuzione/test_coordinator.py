"""Test per distributors/setdistribuzione/coordinator.py: quali giorni
chiedere, orario casuale della richiesta, login solo quando serve, coda dei giorni da riprovare,
ConfigEntryAuthFailed ed esiti di recupera_storico (stesso contratto degli
altri distributori, issue #4)."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest
from freezegun import freeze_time
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.setdistribuzione import (
    coordinator as modulo,
)
from custom_components.contatore_letture.distributors.setdistribuzione import (
    create_coordinator,
)
from custom_components.contatore_letture.distributors.setdistribuzione.api import (
    SetDistribuzioneApiError,
)
from custom_components.contatore_letture.distributors.setdistribuzione.auth import (
    SetDistribuzioneInvalidCredentials,
)
from custom_components.contatore_letture.distributors.setdistribuzione.const import (
    CONF_DATA_INSTALLAZIONE,
    CONF_EMAIL,
    CONF_GIORNI_DA_RIPROVARE,
    CONF_ORARIO_RICHIESTA,
    CONF_PASSWORD,
    CONF_PODS,
    orario_casuale,
)

from .dati_reali import CONSUMI_GIORNO_12_10_2026

POD = "IT999E00000001"
RIFERIMENTO = {
    "pod": POD, "fiscal_code": "XXXX", "profile": "Retail_SET",
    "contract_id": "0100000001", "nome": "Casa",
}
ORE_REALI = CONSUMI_GIORNO_12_10_2026["data"]["consumptions"]


def _ore_del(giorno: date) -> list[dict]:
    """Le ore reali del 12/10, spostate sul giorno chiesto."""
    return [
        {**ora, "year": giorno.year, "month": giorno.month, "day": giorno.day}
        for ora in ORE_REALI
    ]


def _entry(hass, *, data=None, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "setdistribuzione",
            CONF_EMAIL: "a@b.it",
            CONF_PASSWORD: "x",
            CONF_PODS: [POD],
            **(data or {}),
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def coordinator(hass):
    """Coordinator già avviato in passato (data di installazione presente),
    richiesta alle 19:00 ora di Roma: alle 20:00 UTC (22:00 a Roma) dei
    test del ciclo è già ora di chiedere."""
    await hass.config.async_set_time_zone("Europe/Rome")
    entry = _entry(
        hass,
        data={CONF_DATA_INSTALLAZIONE: "2026-10-01"},
        options={CONF_ORARIO_RICHIESTA: "19:00"},
    )
    return create_coordinator(hass, entry)


@pytest.fixture
def statistiche(monkeypatch):
    """Statistiche finte: ultima data configurabile, import registrato."""
    stato = {"ultima": None, "importate": []}

    async def _ultima(hass, pod):
        return stato["ultima"]

    async def _importa(hass, pod, ore):
        stato["importate"].append(ore)

    monkeypatch.setattr(modulo, "async_get_ultima_data_disponibile", _ultima)
    monkeypatch.setattr(modulo, "async_import_ore", _importa)
    return stato


def _api_finta(consumi=None, errore_il: date | None = None):
    """API finta: per ogni giorno le ore reali, o quelle di 'consumi'."""
    api = Mock()
    api.async_get_pod_account = AsyncMock(return_value=[RIFERIMENTO])

    async def _consumi(fiscal_code, profilo, contract_id, pod, giorno):
        if giorno == errore_il:
            raise SetDistribuzioneApiError("HTTP 500")
        if consumi is not None:
            return consumi.get(giorno, [])
        return _ore_del(giorno)

    api.async_get_consumi_giorno = AsyncMock(side_effect=_consumi)
    return api


def _giorni_chiesti(api) -> list[date]:
    return [c.args[4] for c in api.async_get_consumi_giorno.call_args_list]


class TestLogin:
    async def test_credenziali_invalide(self, hass, coordinator, monkeypatch):
        monkeypatch.setattr(
            coordinator._auth, "async_login",
            AsyncMock(side_effect=SetDistribuzioneInvalidCredentials("password errata")),
        )
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_login()


@freeze_time("2026-10-13 20:00:00")
class TestCicloAutomatico:
    async def test_chiede_ieri_e_lo_importa(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta()
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        dati = await coordinator._async_update_data()

        assert _giorni_chiesti(api) == [date(2026, 10, 12)]
        assert len(statistiche["importate"][0]) == 24
        assert dati["by_pod"][POD]["ultimo_giorno_importato"] == "2026-10-12"
        assert dati["by_pod"][POD]["kwh_ultimo_giorno_importato"] == pytest.approx(7.858, abs=1e-3)
        assert CONF_GIORNI_DA_RIPROVARE not in coordinator.entry.data or not coordinator.entry.data[
            CONF_GIORNI_DA_RIPROVARE
        ]

    async def test_nessun_login_se_ieri_e_gia_importato(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        statistiche["ultima"] = date(2026, 10, 12)
        login = AsyncMock()
        monkeypatch.setattr(coordinator, "_async_login", login)

        dati = await coordinator._async_update_data()

        login.assert_not_called()
        assert dati["by_pod"][POD]["ultima_data_disponibile"] == "2026-10-12"

    async def test_giorno_non_pubblicato_va_in_coda_e_viene_riprovato(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        api = _api_finta(consumi={})
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))
        await coordinator._async_update_data()
        assert set(coordinator.entry.data[CONF_GIORNI_DA_RIPROVARE][POD]) == {"2026-10-12"}

        # Il giorno dopo: si chiedono sia l'arretrato sia il nuovo atteso.
        api = _api_finta()
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))
        with freeze_time("2026-10-14 20:00:00"):
            await coordinator._async_update_data()
        assert _giorni_chiesti(api) == [date(2026, 10, 12), date(2026, 10, 13)]
        assert not coordinator.entry.data[CONF_GIORNI_DA_RIPROVARE]

    async def test_giorno_incompleto_va_in_coda(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta(consumi={date(2026, 10, 12): _ore_del(date(2026, 10, 12))[:18]})
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        await coordinator._async_update_data()

        assert statistiche["importate"] == []
        assert "2026-10-12" in coordinator.entry.data[CONF_GIORNI_DA_RIPROVARE][POD]

    async def test_errore_api_salva_il_ricevuto_e_accoda_il_resto(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        coordinator.hass.config_entries.async_update_entry(
            coordinator.entry,
            data={**coordinator.entry.data,
                  CONF_GIORNI_DA_RIPROVARE: {POD: {"2026-10-10": "2026-10-11"}}},
        )
        api = _api_finta(errore_il=date(2026, 10, 12))
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        assert len(statistiche["importate"][0]) == 24  # il 10/10 è arrivato
        assert set(coordinator.entry.data[CONF_GIORNI_DA_RIPROVARE][POD]) == {"2026-10-12"}

    async def test_pod_non_piu_nell_account(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta()
        api.async_get_pod_account = AsyncMock(return_value=[])
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        with pytest.raises(UpdateFailed, match="non risulta"):
            await coordinator._async_update_data()
        assert "2026-10-12" in coordinator.entry.data[CONF_GIORNI_DA_RIPROVARE][POD]


class TestOrarioRichiesta:
    def test_orario_casuale_tra_le_18_e_le_22(self):
        for _ in range(200):
            assert "18:00" <= orario_casuale() <= "22:00"

    async def test_entry_senza_orario_ne_riceve_uno_casuale(self, hass):
        coordinator = create_coordinator(hass, _entry(hass))
        orario = coordinator.entry.options[CONF_ORARIO_RICHIESTA]
        assert "18:00" <= orario <= "22:00"

    async def test_orario_esistente_non_si_tocca(self, hass, coordinator):
        assert coordinator.entry.options[CONF_ORARIO_RICHIESTA] == "19:00"

    @freeze_time("2026-10-13 15:00:00")  # 17:00 a Roma
    async def test_prima_dell_orario_nessun_login(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        login = AsyncMock()
        monkeypatch.setattr(coordinator, "_async_login", login)

        await coordinator._async_update_data()

        login.assert_not_called()

    @freeze_time("2026-10-13 08:00:00")  # 10:00 a Roma
    async def test_primo_avvio_chiede_subito(self, hass, statistiche, monkeypatch):
        await hass.config.async_set_time_zone("Europe/Rome")
        coordinator = create_coordinator(
            hass, _entry(hass, options={CONF_ORARIO_RICHIESTA: "19:00"})
        )
        api = _api_finta()
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        await coordinator._async_update_data()

        assert _giorni_chiesti(api) == [date(2026, 10, 12)]
        assert coordinator.entry.data[CONF_DATA_INSTALLAZIONE] == "2026-10-13"

        # Al ciclo dopo, ancora prima dell'orario: si aspetta.
        api = _api_finta(consumi={})
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))
        statistiche["ultima"] = None
        await coordinator._async_update_data()
        api.async_get_consumi_giorno.assert_not_called()


class TestRecuperaStorico:
    async def test_importa_a_blocchi(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta()
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        await coordinator.async_recupera_storico(date(2026, 8, 1), date(2026, 9, 30))

        assert len(_giorni_chiesti(api)) == 61
        # 61 giorni = blocchi da 31 + 30: due scritture, non 61.
        assert [len(ore) // 24 for ore in statistiche["importate"]] == [31, 30]

    async def test_nessun_dato_solleva_errore_visibile(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        monkeypatch.setattr(
            coordinator, "_async_login", AsyncMock(return_value=_api_finta(consumi={}))
        )
        with pytest.raises(HomeAssistantError, match="Nessun dato importato"):
            await coordinator.async_recupera_storico(date(2026, 8, 1), date(2026, 8, 3))

    async def test_intervallo_troppo_ampio(self, hass, coordinator):
        with pytest.raises(ServiceValidationError, match="troppo ampio"):
            await coordinator.async_recupera_storico(date(2024, 1, 1), date(2026, 1, 1))

    async def test_pod_non_configurato(self, hass, coordinator):
        with pytest.raises(ServiceValidationError, match="non è configurato"):
            await coordinator.async_recupera_storico(
                date(2026, 8, 1), date(2026, 8, 2), pod="IT999E00000002"
            )
