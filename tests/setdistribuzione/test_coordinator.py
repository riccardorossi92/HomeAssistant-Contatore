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
    stato = {
        "ultima": None,
        "importate": [],
        "immissione_esistente": False,
    }

    async def _ultima(hass, pod):
        return stato["ultima"]

    async def _esiste_immissione(hass, pod):
        return stato["immissione_esistente"]

    async def _importa(hass, pod, ore, *, immissione=False):
        stato["immesse" if immissione else "importate"].append(ore)

    stato["immesse"] = []

    monkeypatch.setattr(modulo, "async_get_ultima_data_disponibile", _ultima)
    monkeypatch.setattr(modulo, "async_esiste_immissione", _esiste_immissione)
    monkeypatch.setattr(modulo, "async_import_ore", _importa)
    return stato


MESI_CON_IMPIANTO = [
    {"month": 3, "total": 0, "estimated": True},
    {"month": 4, "total": 339.108, "estimated": False},
]
MESI_SENZA_IMPIANTO = [{"month": m, "total": 0, "estimated": True} for m in range(1, 13)]


def _api_finta(consumi=None, errore_il: date | None = None, mesi_a2=None, errore_a2=False):
    """API finta: per ogni giorno le ore reali, o quelle di 'consumi'.
    L'immissione (A2) è la stessa curva; 'mesi_a2' decide se c'è un impianto."""
    api = Mock()
    api.async_get_pod_account = AsyncMock(return_value=[RIFERIMENTO])

    async def _consumi(fiscal_code, profilo, contract_id, pod, giorno, grandezza="A1"):
        if giorno == errore_il or (grandezza == "A2" and errore_a2):
            raise SetDistribuzioneApiError("HTTP 500")
        if consumi is not None:
            return consumi.get(giorno, [])
        return _ore_del(giorno)

    api.async_get_consumi_giorno = AsyncMock(side_effect=_consumi)
    api.async_get_mesi_anno = AsyncMock(
        return_value=MESI_SENZA_IMPIANTO if mesi_a2 is None else mesi_a2
    )
    return api


def _giorni_chiesti(api, grandezza="A1") -> list[date]:
    return [
        c.args[4] for c in api.async_get_consumi_giorno.call_args_list
        if c.kwargs.get("grandezza", "A1") == grandezza
    ]


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


@freeze_time("2026-10-13 20:00:00")
class TestImmissione:
    async def test_senza_impianto_solo_prelievo(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta()
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        dati = await coordinator._async_update_data()

        assert _giorni_chiesti(api, "A2") == []
        assert statistiche["immesse"] == []
        assert dati["by_pod"][POD]["con_impianto"] is False
        # Anno corrente e precedente, entrambi senza mesi misurati.
        assert [c.args[4] for c in api.async_get_mesi_anno.call_args_list] == [2026, 2025]

    async def test_con_impianto_importa_anche_l_immissione(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        api = _api_finta(mesi_a2=MESI_CON_IMPIANTO)
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        dati = await coordinator._async_update_data()

        assert _giorni_chiesti(api, "A2") == [date(2026, 10, 12)]
        assert len(statistiche["immesse"][0]) == 24
        assert dati["by_pod"][POD]["con_impianto"] is True
        assert dati["by_pod"][POD]["ultimo_giorno_immesso"] == "2026-10-12"
        assert dati["by_pod"][POD]["kwh_ultimo_giorno_immesso"] == pytest.approx(7.858, abs=1e-3)

    async def test_riavvio_riprende_impianto_dalla_serie_esistente(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        """Dopo un riavvio, prima del ciclo con login, un POD che ha già
        una serie di immissione resta "con impianto": i sensori non
        diventano non disponibili."""
        statistiche["immissione_esistente"] = True
        statistiche["ultima"] = date(2026, 10, 12)
        login = AsyncMock()
        monkeypatch.setattr(coordinator, "_async_login", login)

        dati = await coordinator._async_update_data()

        login.assert_not_called()
        assert dati["by_pod"][POD]["con_impianto"] is True

    async def test_riavvio_senza_serie_immissione_nessun_impianto(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        statistiche["ultima"] = date(2026, 10, 12)
        login = AsyncMock()
        monkeypatch.setattr(coordinator, "_async_login", login)

        dati = await coordinator._async_update_data()

        login.assert_not_called()
        assert dati["by_pod"][POD]["con_impianto"] is False

    async def test_impianto_riletto_una_volta_al_giorno(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        api = _api_finta(mesi_a2=MESI_CON_IMPIANTO)
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))
        await coordinator._async_update_data()
        statistiche["ultima"] = None
        await coordinator._async_update_data()
        assert api.async_get_mesi_anno.await_count == 1

    async def test_errore_immissione_non_blocca_il_prelievo(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        api = _api_finta(mesi_a2=MESI_CON_IMPIANTO, errore_a2=True)
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        dati = await coordinator._async_update_data()

        assert len(statistiche["importate"][0]) == 24
        assert statistiche["immesse"] == []
        assert dati["by_pod"][POD]["ultimo_giorno_immesso"] is None
        assert not coordinator.entry.data.get(CONF_GIORNI_DA_RIPROVARE)

    async def test_errore_lettura_impianto_solo_prelievo(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        api = _api_finta()
        api.async_get_mesi_anno = AsyncMock(side_effect=SetDistribuzioneApiError("HTTP 500"))
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        dati = await coordinator._async_update_data()

        assert len(statistiche["importate"][0]) == 24
        assert dati["by_pod"][POD]["con_impianto"] is False

    async def test_recupera_storico_con_impianto(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta(mesi_a2=MESI_CON_IMPIANTO)
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))
        coordinator.async_update_listeners = Mock()

        await coordinator.async_recupera_storico(date(2026, 8, 1), date(2026, 9, 30))

        assert len(_giorni_chiesti(api, "A2")) == 61
        assert [len(ore) // 24 for ore in statistiche["immesse"]] == [31, 30]
        assert coordinator.data["by_pod"][POD]["con_impianto"] is True
        assert coordinator.data["by_pod"][POD]["ultimo_giorno_immesso"] == "2026-09-30"


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
        coordinator.async_update_listeners = Mock()

        await coordinator.async_recupera_storico(date(2026, 8, 1), date(2026, 9, 30))

        assert len(_giorni_chiesti(api)) == 61
        coordinator.async_update_listeners.assert_called_once()
        # 61 giorni = blocchi da 31 + 30: due scritture, non 61.
        assert [len(ore) // 24 for ore in statistiche["importate"]] == [31, 30]
        # Anche i sensori "ultimo giorno importato" seguono il recupero,
        # senza aspettare il ciclo automatico serale.
        dati_pod = coordinator.data["by_pod"][POD]
        assert dati_pod["ultimo_giorno_importato"] == "2026-09-30"
        assert dati_pod["kwh_ultimo_giorno_importato"] == pytest.approx(7.858, abs=1e-3)

    async def test_recupero_vecchio_non_torna_indietro(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        coordinator.data = {
            "by_pod": {
                POD: {
                    "ultimo_giorno_importato": "2026-09-30",
                    "kwh_ultimo_giorno_importato": 1.5,
                    "ultimo_giorno_immesso": "2026-09-30",
                    "kwh_ultimo_giorno_immesso": 2.5,
                }
            }
        }
        api = _api_finta(mesi_a2=MESI_CON_IMPIANTO)
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))
        coordinator.async_update_listeners = Mock()

        await coordinator.async_recupera_storico(date(2026, 8, 1), date(2026, 8, 31))

        dati_pod = coordinator.data["by_pod"][POD]
        assert dati_pod["ultimo_giorno_importato"] == "2026-09-30"
        assert dati_pod["kwh_ultimo_giorno_importato"] == pytest.approx(1.5)
        assert dati_pod["ultimo_giorno_immesso"] == "2026-09-30"
        assert dati_pod["kwh_ultimo_giorno_immesso"] == pytest.approx(2.5)

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
