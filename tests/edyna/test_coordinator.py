"""Test di distributors/edyna/coordinator.py: quando chiedere (una volta al
giorno dopo l'orario, subito al primo avvio), quali mesi leggere, che cosa
importare, login solo quando serve, ConfigEntryAuthFailed ed esiti di
recupera_storico (stesso contratto degli altri distributori, issue #4)."""
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
from custom_components.contatore_letture.distributors.edyna import coordinator as modulo
from custom_components.contatore_letture.distributors.edyna import create_coordinator
from custom_components.contatore_letture.distributors.edyna.api import EdynaApiError
from custom_components.contatore_letture.distributors.edyna.auth import (
    EdynaAuthError,
    EdynaInvalidCredentials,
)
from custom_components.contatore_letture.distributors.edyna.const import (
    CONF_DATA_INSTALLAZIONE,
    CONF_ORARIO_RICHIESTA,
    CONF_PASSWORD,
    CONF_PODS,
    CONF_USERNAME,
    orario_casuale,
)

POD = "IT000E00000001"
ALTRO_POD = "IT000E00000003"


def _riga(base: float = 0.5, ora25: float = 0.0) -> list[float | None]:
    return [base] * 24 + [ora25]


def _entry(hass, *, data=None, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "edyna",
            CONF_USERNAME: "mario",
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
    richiesta alle 09:00 ora di Roma: ai test del ciclo (12:00 UTC = 13:00
    a Roma) è già ora di chiedere."""
    await hass.config.async_set_time_zone("Europe/Rome")
    entry = _entry(
        hass,
        data={CONF_DATA_INSTALLAZIONE: "2026-10-01"},
        options={CONF_ORARIO_RICHIESTA: "09:00"},
    )
    return create_coordinator(hass, entry)


@pytest.fixture
def statistiche(monkeypatch):
    """Statistiche finte: ultima data configurabile, import registrato."""
    stato = {"ultima": None, "importate": []}

    async def _ultima(hass, pod):
        return stato["ultima"]

    async def _importa(hass, pod, ore):
        stato["importate"].append((pod, ore))

    monkeypatch.setattr(modulo, "async_get_ultima_data_disponibile", _ultima)
    monkeypatch.setattr(modulo, "async_import_ore", _importa)
    return stato


def _api_finta(mesi: dict[tuple[int, int], dict[date, list]] | None = None, errore_il=None):
    """API finta: per ogni (anno, mese) le righe indicate; mese sconosciuto -> {}."""
    api = Mock()
    api.async_get_pod_account = AsyncMock(return_value=[{"pod": POD, "nome": "Attivo"}])

    async def _curva(pod, anno, mese):
        if (anno, mese) == errore_il:
            raise EdynaApiError("HTTP 500")
        return (mesi or {}).get((anno, mese), {})

    api.async_get_curva_mese = AsyncMock(side_effect=_curva)
    return api


def _mesi_chiesti(api) -> list[tuple[int, int]]:
    return [(c.args[1], c.args[2]) for c in api.async_get_curva_mese.call_args_list]


def _giorni(anno: int, mese: int, da: int, a: int) -> dict[date, list]:
    return {date(anno, mese, g): _riga() for g in range(da, a + 1)}


class TestLogin:
    async def test_credenziali_invalide(self, hass, coordinator, monkeypatch):
        monkeypatch.setattr(
            coordinator._auth, "async_login",
            AsyncMock(side_effect=EdynaInvalidCredentials("password errata")),
        )
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_login()

    async def test_errore_di_login_generico_e_update_failed(self, hass, coordinator, monkeypatch):
        monkeypatch.setattr(
            coordinator._auth, "async_login", AsyncMock(side_effect=EdynaAuthError("rete"))
        )
        with pytest.raises(UpdateFailed):
            await coordinator._async_login()


@freeze_time("2026-11-05 12:00:00")
class TestCicloAutomatico:
    async def test_primo_avvio_legge_gli_ultimi_due_mesi(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta({(2026, 10): _giorni(2026, 10, 1, 25)})
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        dati = await coordinator._async_update_data()

        assert _mesi_chiesti(api) == [(2026, 10), (2026, 11)]
        assert len(statistiche["importate"]) == 1
        _, ore = statistiche["importate"][0]
        assert len(ore) == 25 * 24 + 1  # il 25/10 ha 25 ore
        assert dati["by_pod"][POD]["ultimo_giorno_importato"] == "2026-10-25"
        assert dati["by_pod"][POD]["kwh_ultimo_giorno_importato"] == pytest.approx(12.0)

    async def test_ripassa_la_settimana_prima_dell_ultimo_dato(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        statistiche["ultima"] = date(2026, 10, 25)
        api = _api_finta({(2026, 10): _giorni(2026, 10, 1, 31)})
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        await coordinator._async_update_data()

        assert _mesi_chiesti(api) == [(2026, 10), (2026, 11)]
        _, ore = statistiche["importate"][0]
        # Dal 18/10 (25 - 7) al 31/10 compresi: 14 giorni, uno da 25 ore.
        assert len(ore) == 14 * 24 + 1

    async def test_ultimo_dato_recente_legge_solo_il_mese_in_corso(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        statistiche["ultima"] = date(2026, 11, 11)
        api = _api_finta({(2026, 11): _giorni(2026, 11, 1, 12)})
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        with freeze_time("2026-11-15 12:00:00"):
            await coordinator._async_update_data()

        # 11/11 - 7 giorni = 4/11: ottobre non serve più.
        assert _mesi_chiesti(api) == [(2026, 11)]

    async def test_nessun_giorno_nuovo_non_importa_niente(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        statistiche["ultima"] = date(2026, 10, 25)
        api = _api_finta({})
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        dati = await coordinator._async_update_data()

        assert statistiche["importate"] == []
        assert dati["by_pod"][POD]["ultima_data_disponibile"] == "2026-10-25"

    async def test_giorni_non_validi_non_si_importano(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        incompleto = _riga()
        incompleto[4] = None
        api = _api_finta({
            (2026, 11): {
                date(2026, 11, 1): _riga(),
                date(2026, 11, 2): incompleto,   # cella vuota
                date(2026, 11, 3): [0.0] * 25,   # non ancora pubblicato
            }
        })
        statistiche["ultima"] = date(2026, 10, 31)
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        dati = await coordinator._async_update_data()

        assert len(statistiche["importate"][0][1]) == 24
        assert dati["by_pod"][POD]["ultimo_giorno_importato"] == "2026-11-01"

    async def test_una_sola_richiesta_al_giorno(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta({(2026, 10): _giorni(2026, 10, 1, 3)})
        login = AsyncMock(return_value=api)
        monkeypatch.setattr(coordinator, "_async_login", login)

        await coordinator._async_update_data()
        await coordinator._async_update_data()

        assert login.await_count == 1

    async def test_il_giorno_dopo_si_richiede_di_nuovo(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta({})
        login = AsyncMock(return_value=api)
        monkeypatch.setattr(coordinator, "_async_login", login)
        with freeze_time("2026-11-05 12:00:00"):
            await coordinator._async_update_data()
        with freeze_time("2026-11-06 12:00:00"):
            await coordinator._async_update_data()
        assert login.await_count == 2

    async def test_prima_dell_orario_non_tocca_il_portale(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        login = AsyncMock()
        monkeypatch.setattr(coordinator, "_async_login", login)
        with freeze_time("2026-11-05 06:00:00"):  # 07:00 a Roma, orario 09:00
            dati = await coordinator._async_update_data()
        login.assert_not_called()
        assert dati["by_pod"][POD]["ultima_data_disponibile"] is None

    async def test_errore_del_portale_riprova_al_ciclo_dopo_e_salva_il_parziale(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        statistiche["ultima"] = date(2026, 10, 25)
        api = _api_finta({(2026, 10): _giorni(2026, 10, 20, 31)}, errore_il=(2026, 11))
        login = AsyncMock(return_value=api)
        monkeypatch.setattr(coordinator, "_async_login", login)

        with pytest.raises(UpdateFailed, match="HTTP 500"):
            await coordinator._async_update_data()
        assert len(statistiche["importate"]) == 1  # ottobre salvato

        # Non conta come richiesta fatta: il ciclo successivo riprova.
        api2 = _api_finta({})
        login.return_value = api2
        await coordinator._async_update_data()
        assert login.await_count == 2

    async def test_arretrato_oltre_il_tetto_si_recupera_a_tranche(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        statistiche["ultima"] = date(2026, 5, 31)  # sei mesi indietro
        api = _api_finta({})
        login = AsyncMock(return_value=api)
        monkeypatch.setattr(coordinator, "_async_login", login)

        await coordinator._async_update_data()
        assert _mesi_chiesti(api) == [(2026, 5), (2026, 6), (2026, 7)]

        # Subito il ciclo dopo (stesso giorno) si continua, senza aspettare domani.
        await coordinator._async_update_data()
        assert login.await_count == 2

    async def test_due_pod_si_leggono_entrambi(self, hass, statistiche, monkeypatch):
        await hass.config.async_set_time_zone("Europe/Rome")
        entry = _entry(
            hass,
            data={CONF_DATA_INSTALLAZIONE: "2026-10-01", CONF_PODS: [POD, ALTRO_POD]},
            options={CONF_ORARIO_RICHIESTA: "09:00"},
        )
        coord = create_coordinator(hass, entry)
        api = _api_finta({(2026, 11): _giorni(2026, 11, 1, 2)})
        monkeypatch.setattr(coord, "_async_login", AsyncMock(return_value=api))
        with freeze_time("2026-11-05 12:00:00"):
            dati = await coord._async_update_data()
        assert [c.args[0] for c in api.async_get_curva_mese.call_args_list].count(ALTRO_POD) == 2
        assert set(dati["by_pod"]) == {POD, ALTRO_POD}


class TestPrimoAvvioEOrario:
    async def test_primo_avvio_chiede_subito_e_ricorda_l_installazione(self, hass, statistiche, monkeypatch):
        await hass.config.async_set_time_zone("Europe/Rome")
        entry = _entry(hass, options={CONF_ORARIO_RICHIESTA: "23:00"})
        coord = create_coordinator(hass, entry)
        api = _api_finta({})
        monkeypatch.setattr(coord, "_async_login", AsyncMock(return_value=api))

        with freeze_time("2026-11-05 08:00:00"):  # molto prima delle 23:00
            await coord._async_update_data()

        assert api.async_get_curva_mese.await_count == 2
        assert entry.data[CONF_DATA_INSTALLAZIONE] == "2026-11-05"

    async def test_orario_casuale_salvato_nelle_opzioni(self, hass):
        entry = _entry(hass)
        create_coordinator(hass, entry)
        h, m = entry.options[CONF_ORARIO_RICHIESTA].split(":")
        assert 6 <= int(h) <= 22 and 0 <= int(m) <= 59

    def test_orario_casuale_nei_limiti(self):
        for _ in range(200):
            h, m = (int(x) for x in orario_casuale().split(":"))
            assert 6 * 60 <= h * 60 + m <= 22 * 60

    async def test_orario_non_valido_nelle_opzioni_usa_un_ripiego(self, hass, caplog):
        entry = _entry(hass, options={CONF_ORARIO_RICHIESTA: "boh"})
        coord = create_coordinator(hass, entry)
        assert coord._orario_richiesta is not None
        assert "non valido" in caplog.text


class TestRecuperaStorico:
    async def test_importa_un_blocco_per_mese(self, hass, coordinator, statistiche, monkeypatch):
        api = _api_finta({
            (2026, 9): _giorni(2026, 9, 1, 30),
            (2026, 10): _giorni(2026, 10, 1, 31),
        })
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))

        await coordinator.async_recupera_storico(date(2026, 9, 15), date(2026, 10, 10))

        assert _mesi_chiesti(api) == [(2026, 9), (2026, 10)]
        giorni = [len(ore) // 24 for _, ore in statistiche["importate"]]
        assert giorni == [16, 10]  # 15-30/9 e 1-10/10

    async def test_nessun_dato_solleva(self, hass, coordinator, statistiche, monkeypatch):
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=_api_finta({})))
        with pytest.raises(HomeAssistantError, match="Nessun dato importato"):
            await coordinator.async_recupera_storico(date(2026, 9, 1), date(2026, 9, 30))

    async def test_un_mese_in_errore_non_ferma_gli_altri(
        self, hass, coordinator, statistiche, monkeypatch
    ):
        api = _api_finta({(2026, 10): _giorni(2026, 10, 1, 5)}, errore_il=(2026, 9))
        monkeypatch.setattr(coordinator, "_async_login", AsyncMock(return_value=api))
        await coordinator.async_recupera_storico(date(2026, 9, 1), date(2026, 10, 31))
        assert len(statistiche["importate"]) == 1

    async def test_pod_non_configurato(self, hass, coordinator):
        with pytest.raises(ServiceValidationError, match="non è configurato"):
            await coordinator.async_recupera_storico(
                date(2026, 9, 1), date(2026, 9, 30), pod="IT000E99999999"
            )

    async def test_date_invertite(self, hass, coordinator):
        with pytest.raises(ServiceValidationError, match="successiva"):
            await coordinator.async_recupera_storico(date(2026, 10, 1), date(2026, 9, 1))

    async def test_intervallo_troppo_ampio(self, hass, coordinator):
        with pytest.raises(ServiceValidationError, match="troppo ampio"):
            await coordinator.async_recupera_storico(date(2020, 1, 1), date(2026, 10, 1))


def test_mesi_nel_periodo():
    assert modulo.mesi_nel_periodo(date(2025, 11, 20), date(2026, 2, 1)) == [
        (2025, 11), (2025, 12), (2026, 1), (2026, 2),
    ]
    assert modulo.mesi_nel_periodo(date(2026, 3, 1), date(2026, 3, 31)) == [(2026, 3)]


def test_mese_precedente():
    assert modulo._mese_precedente(date(2026, 11, 5), 1) == date(2026, 10, 1)
    assert modulo._mese_precedente(date(2026, 1, 20), 1) == date(2025, 12, 1)
    assert modulo._mese_precedente(date(2026, 3, 9), 0) == date(2026, 3, 1)
