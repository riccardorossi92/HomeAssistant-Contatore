"""Test per distributors/ireti/coordinator.py: ciclo automatico (coda dei
giorni da riprovare, ripiego podType, ConfigEntryAuthFailed) ed esiti di
recupera_storico (stesso contratto di PCF/E-Distribuzione/Areti - issue
#4: un fallimento silenzioso deve diventare un errore visibile)."""
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
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture.const import DOMAIN
from custom_components.contatore_letture.distributors.ireti import create_coordinator
from custom_components.contatore_letture.distributors.ireti.api import IretiApiError
from custom_components.contatore_letture.distributors.ireti.auth import IretiInvalidCredentials
from custom_components.contatore_letture.distributors.ireti.const import (
    CONF_GIORNI_DA_RIPROVARE,
    CONF_PASSWORD,
    CONF_PODS,
    CONF_USERNAME,
)

POD = "IT020E00000000001"
DATA_DA = date(2026, 7, 1)
DATA_A = date(2026, 7, 31)

# Forma reale (issue #6, 17/09/2026), sampleValues troncato a 4 campioni
# per brevità (la lunghezza attesa è testata a parte in test_statistics.py).
LOAD_PROFILE_REALE = {
    "loadProfileDate": "20/08/2026 00:00:00 +0200",
    "serialNumber": "serial-fantasia",
    "timeType": "",
    "energyType": "A1",
    "measType": "0",
    "sampleValues": [0.023, 0.022, 0.018, 0.012],
}


def _load_profile(giorno: date, sample_values=None) -> dict:
    """Stessa forma di LOAD_PROFILE_REALE, ma per una data a scelta - serve
    a costruire risposte allineate al giorno effettivamente richiesto
    (_prossima_richiesta), non sempre il 20/08/2026 fisso."""
    return {
        "loadProfileDate": giorno.strftime("%d/%m/%Y") + " 00:00:00 +0200",
        "serialNumber": "serial-fantasia",
        "timeType": "",
        "energyType": "A1",
        "measType": "0",
        "sampleValues": sample_values if sample_values is not None else [0.023, 0.022, 0.018, 0.012],
    }


@pytest.fixture
async def coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "ireti",
            CONF_USERNAME: "utente",
            CONF_PASSWORD: "x",
            CONF_PODS: [POD],
        },
    )
    entry.add_to_hass(hass)
    return create_coordinator(hass, entry)


class TestLogin:
    async def test_credenziali_invalide_solleva_configentryauthfailed(self, hass, coordinator, monkeypatch):
        monkeypatch.setattr(
            coordinator._auth, "async_login",
            AsyncMock(side_effect=IretiInvalidCredentials("invalid_grant")),
        )
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_login()

    async def test_login_riuscito_ritorna_client(self, hass, coordinator, monkeypatch):
        monkeypatch.setattr(
            coordinator._auth, "async_login", AsyncMock(return_value="token-fantasia")
        )
        api = await coordinator._async_login()
        assert api is not None


class TestPodType:
    async def test_ripiego_su_orario_se_history_fallisce(self, hass, coordinator):
        """/users/exabeat/history non e' mai stato confermato con dati
        reali: un suo fallimento non deve bloccare l'import, solo far
        ricadere su 'orario' (unico valore osservato finora)."""
        api = Mock()
        api.async_get_history = AsyncMock(side_effect=IretiApiError("500 dal portale"))

        pod_type = await coordinator._async_pod_type(api, POD, "CF1", "start", "end")

        assert pod_type == "orario"

    async def test_usa_podtype_di_history_se_disponibile(self, hass, coordinator):
        api = Mock()
        api.async_get_history = AsyncMock(return_value={"podType": "fasce"})

        pod_type = await coordinator._async_pod_type(api, POD, "CF1", "start", "end")

        assert pod_type == "fasce"

    async def test_podtype_e_tenuto_in_cache(self, hass, coordinator):
        api = Mock()
        api.async_get_history = AsyncMock(return_value={"podType": "fasce"})

        await coordinator._async_pod_type(api, POD, "CF1", "start", "end")
        await coordinator._async_pod_type(api, POD, "CF1", "start", "end")

        api.async_get_history.assert_awaited_once()


class TestUpdateData:
    """Ciclo automatico: coda dei giorni da riprovare (per-POD), non più
    una finestra fissa - vedi coordinator.py e tests/ireti/test_coordinator_coda.py
    per i test della coda in isolamento (_accoda_giorno/_rimuovi_dalla_coda/
    _scrivi_code). Qui si esercita l'integrazione end-to-end con
    _async_update_data, sempre con l'orologio fermo (freeze_time) perché
    _prossima_richiesta dipende da 'oggi'."""

    def _api(self, load_profiles):
        api = Mock()
        api.async_get_consumer = AsyncMock(return_value={"codFiscale": "CF1", "pIVA": None})
        api.async_get_history = AsyncMock(return_value={"podType": "orario"})
        api.async_get_measures_loadprofiles = AsyncMock(return_value=load_profiles)
        return api

    async def test_giorno_presente_viene_importato_e_non_va_in_coda(
        self, hass, coordinator, monkeypatch
    ):
        import custom_components.contatore_letture.distributors.ireti.coordinator as mod

        atteso = date(2026, 9, 17)  # oggi - RITARDO_DATI_GIORNI (1), con oggi = 18/09
        api = self._api([_load_profile(atteso)])
        coordinator._async_login = AsyncMock(return_value=api)

        monkeypatch.setattr(mod, "async_import_curva_giorni", AsyncMock(return_value=None))
        monkeypatch.setattr(mod, "async_get_ultima_data_disponibile", AsyncMock(return_value=None))

        with freeze_time("2026-09-18 12:00:00"):
            dati = await coordinator._async_update_data()
            code = coordinator._leggi_code()

        assert dati["by_pod"][POD]["ultimo_giorno_importato"] == "2026-09-17"
        assert dati["by_pod"][POD]["kwh_ultimo_giorno_importato"] == pytest.approx(0.075)
        assert POD not in code or "2026-09-17" not in code.get(POD, {})

    async def test_giorno_mancante_va_in_coda(self, hass, coordinator, monkeypatch):
        """Il caso che prima (finestra fissa) passava silenzioso: un
        giorno senza dati deve finire in coda, non sparire e basta."""
        import custom_components.contatore_letture.distributors.ireti.coordinator as mod

        api = self._api([])  # nessun dato per il giorno atteso
        coordinator._async_login = AsyncMock(return_value=api)

        monkeypatch.setattr(mod, "async_import_curva_giorni", AsyncMock(return_value=None))
        monkeypatch.setattr(mod, "async_get_ultima_data_disponibile", AsyncMock(return_value=None))

        with freeze_time("2026-09-18 12:00:00"):
            dati = await coordinator._async_update_data()
            code = coordinator._leggi_code()

        assert dati["by_pod"][POD]["ultimo_giorno_importato"] is None
        assert set(code[POD]) == {"2026-09-17"}

    async def test_ciclo_successivo_chiede_dal_giorno_in_coda(self, hass, coordinator, monkeypatch):
        """Un giorno rimasto arretrato deve essere richiesto di nuovo (in
        un range che parte da lui), non abbandonato al giro successivo."""
        import custom_components.contatore_letture.distributors.ireti.coordinator as mod

        arretrato = date(2026, 9, 15)
        api = self._api([_load_profile(arretrato)])
        coordinator._async_login = AsyncMock(return_value=api)

        monkeypatch.setattr(mod, "async_import_curva_giorni", AsyncMock(return_value=None))
        monkeypatch.setattr(mod, "async_get_ultima_data_disponibile", AsyncMock(return_value=None))
        hass.config_entries.async_update_entry(
            coordinator.entry,
            data={
                **coordinator.entry.data,
                CONF_GIORNI_DA_RIPROVARE: {POD: {"2026-09-15": "2026-09-16"}},
            },
        )

        with freeze_time("2026-09-18 12:00:00"):
            await coordinator._async_update_data()

        richiesta = api.async_get_measures_loadprofiles.call_args
        # pod, tax_code, pod_type, start_iso, end_iso
        assert richiesta.args[3].startswith("2026-09-15")

    async def test_errore_measures_solleva_updatefailed_e_accoda(
        self, hass, coordinator, monkeypatch
    ):
        from homeassistant.helpers.update_coordinator import UpdateFailed

        api = Mock()
        api.async_get_consumer = AsyncMock(return_value={"codFiscale": "CF1", "pIVA": None})
        api.async_get_history = AsyncMock(return_value={"podType": "orario"})
        api.async_get_measures_loadprofiles = AsyncMock(
            side_effect=IretiApiError("500 dal portale")
        )
        coordinator._async_login = AsyncMock(return_value=api)

        import custom_components.contatore_letture.distributors.ireti.coordinator as mod

        monkeypatch.setattr(mod, "async_get_ultima_data_disponibile", AsyncMock(return_value=None))

        with freeze_time("2026-09-18 12:00:00"), pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        with freeze_time("2026-09-18 12:00:00"):
            code = coordinator._leggi_code()
        # Un errore di trasporto non deve far perdere il giorno: finisce
        # comunque in coda per il ciclo successivo.
        assert set(code[POD]) == {"2026-09-17"}


class TestRecuperaStorico:
    @pytest.fixture
    def _import_statistiche(self, monkeypatch):
        import custom_components.contatore_letture.distributors.ireti.coordinator as mod

        mock = AsyncMock(return_value=None)
        monkeypatch.setattr(mod, "async_import_curva_giorni", mock)
        return mock

    @pytest.fixture(autouse=True)
    def _login_e_tax_code(self, coordinator):
        api = Mock()
        coordinator._async_login = AsyncMock(return_value=api)
        coordinator._async_customer_tax_code_vat = AsyncMock(return_value="CF1")
        coordinator._async_pod_type = AsyncMock(return_value="orario")
        self._api = api

    async def test_pod_non_configurato_solleva_servicevalidationerror(self, hass, coordinator):
        with pytest.raises(ServiceValidationError):
            await coordinator.async_recupera_storico(DATA_DA, DATA_A, pod="IT999X99999999")

    async def test_data_da_dopo_data_a_solleva_servicevalidationerror(self, hass, coordinator):
        with pytest.raises(ServiceValidationError):
            await coordinator.async_recupera_storico(DATA_A, DATA_DA)

    async def test_errore_api_fa_fallire_l_azione(self, hass, coordinator, _import_statistiche):
        self._api.async_get_measures_loadprofiles = AsyncMock(
            side_effect=IretiApiError("500 dal portale")
        )
        with pytest.raises(HomeAssistantError, match="500 dal portale"):
            await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    async def test_nessun_giorno_disponibile_fa_fallire_l_azione(
        self, hass, coordinator, _import_statistiche
    ):
        self._api.async_get_measures_loadprofiles = AsyncMock(return_value=[])
        with pytest.raises(HomeAssistantError, match="[Nn]essun"):
            await coordinator.async_recupera_storico(DATA_DA, DATA_A)

    async def test_successo_non_solleva(self, hass, coordinator, _import_statistiche):
        self._api.async_get_measures_loadprofiles = AsyncMock(return_value=[LOAD_PROFILE_REALE])

        await coordinator.async_recupera_storico(DATA_DA, DATA_A)

        _import_statistiche.assert_awaited()
