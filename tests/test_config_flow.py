"""Test del config flow: wizard ARERA (regione->provincia->comune->lookup),
ramo PCF (credenziali + POD), ramo E-Distribuzione (login/OTP/POD),
options flow e reauth.

Tutto cio' che tocca la rete e' sostituito: async_get_comuni_tree (elenco
ISTAT), async_query_distributore (lookup ARERA), le funzioni di
validazione credenziali/POD del modulo distributore, e i client
EdistribuzioneAuthClient / EdistribuzioneApiClient.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contatore_letture import config_flow as cf
from custom_components.contatore_letture.arera_lookup import AreraLookupError
from custom_components.contatore_letture.const import (
    CONF_CLIENT_ID,
    CONF_PODS,
    CONF_SECRET_ID,
    DOMAIN,
)
from custom_components.contatore_letture.distributors import duereti, retipiu, revv, unareti
from custom_components.contatore_letture.distributors.edistribuzione import (
    api as edist_api,
)
from custom_components.contatore_letture.distributors.edistribuzione import (
    auth as edist_auth,
)
from custom_components.contatore_letture.distributors.edistribuzione import (
    const as edist_const,
)
from custom_components.contatore_letture.distributors.edistribuzione.auth import (
    EdistribuzioneBloccoAntibot,
    EdistribuzioneErroreTecnico,
    EdistribuzioneInvalidCredentials,
    EdistribuzioneInvalidOtp,
    EdistribuzioneParsingError,
    EdistribuzionePasswordScaduta,
    EdistribuzioneTroppeSessioni,
)
from custom_components.contatore_letture.distributors.edistribuzione.const import (
    CONF_ORA_RICHIESTA,
    CONF_ORARIO_RICHIESTA,
    CONF_REFRESH_TOKEN,
)
from custom_components.contatore_letture.distributors.ireti import api as ireti_api
from custom_components.contatore_letture.distributors.ireti import auth as ireti_auth
from custom_components.contatore_letture.distributors.ireti.auth import (
    IretiInvalidCredentials,
)
from custom_components.contatore_letture.distributors.ireti.const import (
    CONF_PASSWORD as IRETI_CONF_PASSWORD,
)
from custom_components.contatore_letture.distributors.ireti.const import (
    CONF_USERNAME as IRETI_CONF_USERNAME,
)
from custom_components.contatore_letture.distributors.pcf_common.const import (
    CONF_PENDING_DATA_A,
    CONF_PENDING_DATA_DA,
    CONF_PENDING_TICKET,
)
from custom_components.contatore_letture.distributors.setdistribuzione import api as set_api
from custom_components.contatore_letture.distributors.setdistribuzione import auth as set_auth
from custom_components.contatore_letture.distributors.setdistribuzione.auth import (
    SetDistribuzioneInvalidCredentials,
)
from custom_components.contatore_letture.distributors.setdistribuzione.const import (
    CONF_EMAIL as SET_CONF_EMAIL,
)
from custom_components.contatore_letture.distributors.setdistribuzione.const import (
    CONF_PASSWORD as SET_CONF_PASSWORD,
)

FAKE_TREE = {
    "Lombardia": {
        "Milano": {
            "Vimodrone": {
                "codice_regione": "03",
                "codice_provincia": "015",
                "codice_comune": "015242",
            },
        },
    },
}

OP_DUERETI = {"ragione_sociale": "DUERETI S.P.A.", "piva": duereti.PIVA}
OP_UNARETI = {"ragione_sociale": "UNARETI S.P.A.", "piva": unareti.PIVA}
OP_RETIPIU = {"ragione_sociale": "RETIPIU' S.R.L.", "piva": retipiu.PIVA}
OP_REVV = {"ragione_sociale": "RETI VALTELLINA VALCHIAVENNA S.R.L.", "piva": revv.PIVA}
OP_SCONOSCIUTO = {"ragione_sociale": "ACME Energia", "piva": "00000000000"}


@pytest.fixture(autouse=True)
def _mock_setup_entry():
    """Evita che una CREATE_ENTRY (o un async_reload dopo reauth/opzioni)
    faccia partire il setup reale della entry - coordinator, sessione
    aiohttp, primo refresh: qui interessa solo l'esito del flow."""
    with patch(
        "custom_components.contatore_letture.async_setup_entry", return_value=True
    ):
        yield


@pytest.fixture
def flow_mocks(monkeypatch):
    """Sostituisce ISTAT + ARERA + validazioni. Default: ARERA trova Duereti,
    credenziali e POD validi. La verifica del POD restituisce il job accodato
    per l'ultimo mese solare concluso: (ticket, data_da, data_a)."""
    query = AsyncMock(return_value=[OP_DUERETI])
    monkeypatch.setattr(cf, "async_get_comuni_tree", AsyncMock(return_value=FAKE_TREE))
    monkeypatch.setattr(cf, "async_query_distributore", query)
    monkeypatch.setattr(cf, "pod_gia_configurato", Mock(return_value=None))
    monkeypatch.setattr(duereti, "async_valida_credenziali", AsyncMock(return_value=None))
    monkeypatch.setattr(
        duereti,
        "async_valida_pod",
        AsyncMock(return_value=(None, ("TCK", date(2026, 8, 1), date(2026, 8, 31)))),
    )
    return {"query": query}


async def _fino_a_comune(hass):
    """user -> provincia -> comune (che fa scattare il lookup ARERA)."""
    res = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"regione": "Lombardia"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"provincia": "Milano"}
    )
    return await hass.config_entries.flow.async_configure(
        res["flow_id"], {"comune": "Vimodrone"}
    )


# --- wizard ARERA -----------------------------------------------------------

async def test_un_distributore_supportato_va_a_distributor_info(hass, flow_mocks):
    res = await _fino_a_comune(hass)
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "distributor_info"


async def test_piu_distributori_supportati_chiede_di_scegliere(hass, flow_mocks):
    flow_mocks["query"].return_value = [OP_DUERETI, OP_UNARETI]
    res = await _fino_a_comune(hass)
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "choose_distributor"


async def test_lookup_arera_fallito_va_a_manual_select(hass, flow_mocks):
    flow_mocks["query"].side_effect = AreraLookupError("ARERA irraggiungibile")
    res = await _fino_a_comune(hass)
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "manual_select"


async def test_nessun_operatore_va_a_manual_select(hass, flow_mocks):
    flow_mocks["query"].return_value = []
    res = await _fino_a_comune(hass)
    assert res["step_id"] == "manual_select"


async def test_operatore_non_supportato_va_a_manual_select(hass, flow_mocks):
    flow_mocks["query"].return_value = [OP_SCONOSCIUTO]
    res = await _fino_a_comune(hass)
    assert res["step_id"] == "manual_select"


@pytest.mark.parametrize(
    ("operatore", "modulo", "chiave"),
    [(OP_RETIPIU, retipiu, "retipiu"), (OP_REVV, revv, "revv")],
)
async def test_nuovi_pcf_usano_il_ramo_pcf_col_proprio_modulo(
    hass, flow_mocks, monkeypatch, operatore, modulo, chiave
):
    """RetiPiù / RE.V.V. riconosciuti da ARERA per P.IVA: stesso ramo "pcf"
    di Duereti/Unareti, ma le validazioni passano dal proprio modulo (quindi
    dal proprio BASE_URL), non da quello di Duereti."""
    flow_mocks["query"].return_value = [operatore]
    monkeypatch.setattr(modulo, "async_valida_credenziali", AsyncMock(return_value=None))
    monkeypatch.setattr(
        modulo,
        "async_valida_pod",
        AsyncMock(return_value=(None, ("TCK", date(2026, 8, 1), date(2026, 8, 31)))),
    )
    res = await _fino_a_comune(hass)
    assert res["step_id"] == "distributor_info"
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {})
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "cid", CONF_SECRET_ID: "sid"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"],
        {"pod": "IT001E00000001", "df": "RSSMRA80A01H501U", "aggiungi_altro": False},
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"]["distributor"] == chiave
    modulo.async_valida_credenziali.assert_awaited_once()
    duereti.async_valida_credenziali.assert_not_awaited()


# --- ramo PCF -------------------------------------------------------------

async def _fino_a_pcf_credentials(hass):
    res = await _fino_a_comune(hass)
    return await hass.config_entries.flow.async_configure(res["flow_id"], {})


async def test_pcf_happy_path_crea_entry_con_ticket(hass, flow_mocks):
    res = await _fino_a_pcf_credentials(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "cid", CONF_SECRET_ID: "sid"}
    )
    assert res["step_id"] == "pcf_add_pod"

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"],
        {"pod": "IT001E00000001", "df": "RSSMRA80A01H501U", "aggiungi_altro": False},
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"]["distributor"] == "duereti"
    assert res["data"][CONF_PODS] == [{"pod": "IT001E00000001", "df": "RSSMRA80A01H501U"}]
    # il ticket di verifica POD viene salvato come pendente, con il periodo
    # (l'ultimo mese solare concluso) su cui è stato accodato
    assert res["data"][CONF_PENDING_TICKET] == "TCK"
    assert res["data"][CONF_PENDING_DATA_DA] == "2026-08-01"
    assert res["data"][CONF_PENDING_DATA_A] == "2026-08-31"


async def test_pcf_credenziali_non_valide_mostra_errore(hass, flow_mocks):
    duereti.async_valida_credenziali.return_value = "invalid_auth"
    res = await _fino_a_pcf_credentials(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "x", CONF_SECRET_ID: "y"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "pcf_credentials"
    assert res["errors"] == {"base": "invalid_auth"}


async def test_pcf_pod_duplicato_mostra_errore(hass, flow_mocks):
    cf.pod_gia_configurato.return_value = "IT001E00000001"
    res = await _fino_a_pcf_credentials(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "cid", CONF_SECRET_ID: "sid"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"pod": "IT001E00000001", "df": "DF", "aggiungi_altro": False}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"pod": "pod_duplicato"}


async def test_pcf_aggiungi_altro_pod_poi_crea_entry(hass, flow_mocks):
    res = await _fino_a_pcf_credentials(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "cid", CONF_SECRET_ID: "sid"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"pod": "IT001E00000001", "df": "DF1", "aggiungi_altro": True}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "pcf_add_pod"

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"pod": "IT001E00000002", "df": "DF2", "aggiungi_altro": False}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert [p["pod"] for p in res["data"][CONF_PODS]] == [
        "IT001E00000001",
        "IT001E00000002",
    ]


# --- options flow -------------------------------------------------------------

def _entry_pcf(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "duereti",
            CONF_CLIENT_ID: "cid",
            CONF_SECRET_ID: "sid",
            CONF_PODS: [{"pod": "IT001E00000001", "df": "DF"}],
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_opzioni_menu_pcf(hass):
    """Dal passaggio al modello a mese chiuso il ramo PCF non ha più la voce
    'orario': i dati non si pubblicano a una certa ora del giorno."""
    entry = _entry_pcf(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == FlowResultType.MENU
    assert set(res["menu_options"]) == {"aggiungi_pod", "rimuovi_pod"}


async def test_opzioni_orario_salva_valore(hass):
    """Lo step 'orario' resta per E-Distribuzione (import ancora giornaliero)."""
    entry = _entry_edist(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"next_step_id": "orario"}
    )
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {CONF_ORARIO_RICHIESTA: "21:35:00"}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ORARIO_RICHIESTA] == "21:35"


async def test_opzioni_orario_sostituisce_il_vecchio_formato(hass):
    """Una entry con la sola ora (formato precedente) la vede come orario
    attuale, e al salvataggio passa al formato HH:MM."""
    entry = _entry_edist(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_ORA_RICHIESTA: 21})
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"next_step_id": "orario"}
    )
    assert res["description_placeholders"] == {"orario_attuale": "21:00"}
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {CONF_ORARIO_RICHIESTA: "17:05:00"}
    )
    assert entry.options == {CONF_ORARIO_RICHIESTA: "17:05"}


async def test_opzioni_rimuovi_pod_non_tutti(hass):
    entry = _entry_pcf(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"next_step_id": "rimuovi_pod"}
    )
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"pods_da_rimuovere": ["IT001E00000001"]}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"pods_da_rimuovere": "non_puoi_rimuoverli_tutti"}


# --- reauth PCF -------------------------------------------------------------

async def test_reauth_pcf_aggiorna_credenziali(hass, flow_mocks):
    entry = _entry_pcf(hass)
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert res["step_id"] == "reauth_confirm"

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_CLIENT_ID: "nuovo", CONF_SECRET_ID: "nuovo2"}
    )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "reauth_successful"
    assert entry.data[CONF_CLIENT_ID] == "nuovo"


# --- ramo E-Distribuzione (login -> OTP -> POD) ----------------------------

@pytest.fixture
def edist_mocks(monkeypatch):
    """Sostituisce EdistribuzioneAuthClient / EdistribuzioneApiClient e le
    sessioni aiohttp. Default: login e OTP ok, un solo POD sull'account."""
    auth = Mock()
    auth.async_begin_login = AsyncMock(return_value=None)
    auth.otp_invio_confermato = True
    auth.async_resend_otp = AsyncMock(return_value=True)
    auth.async_submit_otp = AsyncMock(
        return_value=SimpleNamespace(access_token="acc", refresh_token="ref-nuovo")
    )
    auth.async_refresh_access_token = AsyncMock(
        return_value=SimpleNamespace(access_token="acc2", refresh_token="ref-nuovo")
    )
    api = Mock()
    api.async_get_supplies = AsyncMock(return_value=[{"IdPod": "IT001E00000009"}])

    monkeypatch.setattr(edist_auth, "EdistribuzioneAuthClient", Mock(return_value=auth))
    monkeypatch.setattr(edist_api, "EdistribuzioneApiClient", Mock(return_value=api))
    monkeypatch.setattr(cf, "async_create_clientsession", lambda *a, **k: Mock())
    monkeypatch.setattr(cf, "async_get_clientsession", lambda *a, **k: Mock())
    # Lo scaricamento automatico e' disattivato nel wizard da ottobre 2026
    # (antibot, issue #11): questi test coprono il ramo login -> OTP -> POD,
    # che torna in uso appena lo si riattiva.
    monkeypatch.setattr(edist_const, "SCARICAMENTO_AUTOMATICO_DISPONIBILE", True)
    return SimpleNamespace(auth=auth, api=api)


async def _fino_a_edist_user(hass):
    """user -> ... -> distributor_info -> submit, con ARERA che dà un
    operatore non supportato e scelta manuale di E-Distribuzione."""
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"regione": "Lombardia"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"provincia": "Milano"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"comune": "Vimodrone"})
    # manual_select -> scelgo edistribuzione
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"distributor": "edistribuzione"}
    )
    # edistribuzione_modalita (al posto di distributor_info) -> automatica
    assert res["step_id"] == "edistribuzione_modalita"
    return await hass.config_entries.flow.async_configure(
        res["flow_id"], {"modalita": "automatica"}
    )


@pytest.fixture
def _arera_sconosciuto(monkeypatch):
    monkeypatch.setattr(cf, "async_get_comuni_tree", AsyncMock(return_value=FAKE_TREE))
    monkeypatch.setattr(
        cf, "async_query_distributore", AsyncMock(return_value=[OP_SCONOSCIUTO])
    )


async def test_edist_credenziali_non_valide(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.auth.async_begin_login.side_effect = EdistribuzioneInvalidCredentials("no")
    res = await _fino_a_edist_user(hass)
    assert res["step_id"] == "edistribuzione_user"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"base": "invalid_auth_email"}


async def test_edist_pagina_login_cambiata(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.auth.async_begin_login.side_effect = EdistribuzioneParsingError("markup")
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["errors"] == {"base": "cannot_connect"}


async def test_edist_bloccato_dalla_protezione_antibot(
    hass, _arera_sconosciuto, edist_mocks
):
    """La pagina anti-bot Imperva non e' un cambio di markup: va mostrato un
    errore dedicato, non il generico cannot_connect."""
    edist_mocks.auth.async_begin_login.side_effect = EdistribuzioneBloccoAntibot(
        "Pardon Our Interruption"
    )
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "edistribuzione_user"
    assert res["errors"] == {"base": "bloccato_antibot"}


async def test_edist_otp_non_valido(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.auth.async_submit_otp.side_effect = EdistribuzioneInvalidOtp("nope")
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["step_id"] == "edistribuzione_otp"
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "000000"})
    assert res["errors"] == {"base": "invalid_otp"}


async def test_edist_troppe_sessioni_al_login(hass, _arera_sconosciuto, edist_mocks):
    """Credenziali giuste ma account con troppe sessioni aperte: in questo
    stato E-Distribuzione non invia nessun OTP, quindi il flow deve fermarsi
    sul form delle credenziali con un errore che dice cosa fare, non
    proseguire a chiedere un codice che non arrivera' (issue #2)."""
    edist_mocks.auth.async_begin_login.side_effect = EdistribuzioneTroppeSessioni(
        "Hai superato il numero di sessioni simultanee consentite"
    )
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "edistribuzione_user"
    assert res["errors"] == {"base": "troppe_sessioni"}


async def test_edist_errore_tecnico_al_login(hass, _arera_sconosciuto, edist_mocks):
    """"KO: Errore Tecnico" dal portale: errore dedicato sul form delle
    credenziali invece del generico "credenziali errate"."""
    edist_mocks.auth.async_begin_login.side_effect = EdistribuzioneErroreTecnico(
        "KO: Errore Tecnico"
    )
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "edistribuzione_user"
    assert res["errors"] == {"base": "errore_tecnico_portale"}


async def test_edist_otp_form_vuoto_chiede_il_codice(hass, _arera_sconosciuto, edist_mocks):
    """Il campo OTP e' Optional (serve a poter richiedere un nuovo codice a
    campo vuoto): senza codice e senza spunta, il form si ripresenta con un
    errore invece di provare a convalidare una stringa vuota."""
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": ""})
    assert res["step_id"] == "edistribuzione_otp"
    assert res["errors"] == {"base": "otp_mancante"}
    edist_mocks.auth.async_submit_otp.assert_not_called()


async def test_edist_richiesta_nuovo_otp(hass, _arera_sconosciuto, edist_mocks):
    """Spuntando 'richiedi un nuovo codice' il flow ne fa rispedire uno DENTRO
    la stessa sessione di login e ripresenta il form: un OTP generato sul sito
    o nell'app appartiene a un'altra sessione e non potrebbe mai essere
    convalidato qui (issue #2)."""
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"otp": "", "richiedi_nuovo_codice": True}
    )
    edist_mocks.auth.async_resend_otp.assert_awaited_once()
    edist_mocks.auth.async_submit_otp.assert_not_called()
    assert res["step_id"] == "edistribuzione_otp"
    assert res["errors"] == {}
    assert "nuovo codice" in res["description_placeholders"]["avviso"]


async def test_edist_avviso_se_invio_otp_non_confermato(
    hass, _arera_sconosciuto, edist_mocks
):
    """Se il portale non ha confermato l'invio del codice, il form OTP lo dice
    invece di lasciare l'utente in attesa di un SMS/email che non arriva."""
    edist_mocks.auth.otp_invio_confermato = False
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["step_id"] == "edistribuzione_otp"
    assert "non ha confermato" in res["description_placeholders"]["avviso"]


async def test_edist_password_scaduta_abortisce_con_motivo_chiaro(
    hass, _arera_sconosciuto, edist_mocks
):
    """Issue #9: se il portale chiede di cambiare la password, il flusso lo
    dice invece del generico "completamento dell'accesso fallito"."""
    edist_mocks.auth.async_submit_otp.side_effect = EdistribuzionePasswordScaduta("x")
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "edistribuzione_password_scaduta"


async def test_edist_otp_parsing_fallito_abortisce(hass, _arera_sconosciuto, edist_mocks):
    """Se l'OTP e' gia' stato accettato ma un passo successivo del parsing
    fallisce, il flusso deve abortire invece di far ripresentare lo stesso
    OTP (monouso, quindi un retry non potrebbe mai riuscire)."""
    edist_mocks.auth.async_submit_otp.side_effect = EdistribuzioneParsingError("consent page")
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["step_id"] == "edistribuzione_otp"
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "edistribuzione_otp_exchange_failed"


async def test_edist_un_solo_pod_crea_entry_subito(hass, _arera_sconosciuto, edist_mocks):
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"]["distributor"] == "edistribuzione"
    assert res["data"][CONF_PODS] == ["IT001E00000009"]
    assert res["data"][CONF_REFRESH_TOKEN] == "ref-nuovo"
    assert "16:00" <= res["options"][CONF_ORARIO_RICHIESTA] <= "20:00"


async def test_edist_nessun_pod_sull_account_abortisce(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.api.async_get_supplies.return_value = []
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "no_pods_found"


async def test_edist_recupero_pod_fallito_abortisce(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.api.async_get_supplies.side_effect = RuntimeError("boom")
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "edistribuzione_supplies_failed"


async def test_edist_piu_pod_si_scelgono(hass, _arera_sconosciuto, edist_mocks):
    edist_mocks.api.async_get_supplies.return_value = [
        {"IdPod": "IT001E00000009"},
        {"IdPod": "IT001E00000010"},
    ]
    res = await _fino_a_edist_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["step_id"] == "edistribuzione_pod"

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_PODS: ["IT001E00000010"]}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_PODS] == ["IT001E00000010"]


def _entry_edist(hass, pods=("IT001E00000009",)):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "edistribuzione",
            CONF_PODS: list(pods),
            CONF_REFRESH_TOKEN: "ref-vecchio",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_edist_reauth_aggiorna_refresh_token(hass, edist_mocks):
    entry = _entry_edist(hass)
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert res["type"] == FlowResultType.MENU
    assert res["step_id"] == "edistribuzione_reauth"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"next_step_id": "edistribuzione_reauth_user"}
    )
    assert res["step_id"] == "edistribuzione_reauth_user"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"otp": "123456"})
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "ref-nuovo"


async def test_edist_opzioni_menu(hass):
    entry = _entry_edist(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == FlowResultType.MENU
    assert set(res["menu_options"]) == {
        "edistribuzione_aggiungi_pod",
        "edistribuzione_rimuovi_pod",
        "orario",
    }


async def test_edist_opzioni_aggiungi_pod(hass, edist_mocks):
    entry = _entry_edist(hass, pods=["IT001E00000009"])
    edist_mocks.api.async_get_supplies.return_value = [
        {"IdPod": "IT001E00000009"},
        {"IdPod": "IT001E00000010"},
    ]
    res = await hass.config_entries.options.async_init(entry.entry_id)
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"next_step_id": "edistribuzione_aggiungi_pod"}
    )
    assert res["step_id"] == "edistribuzione_aggiungi_pod"
    res = await hass.config_entries.options.async_configure(
        res["flow_id"], {"pods_da_aggiungere": ["IT001E00000010"]}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_PODS] == ["IT001E00000009", "IT001E00000010"]


# --- ramo Ireti (login -> POD dall'account, come edistribuzione ma senza OTP) ---

@pytest.fixture
def ireti_mocks(monkeypatch):
    """Sostituisce IretiAuthClient / IretiApiClient e le sessioni aiohttp.
    Default: login ok, un solo POD sull'account."""
    auth = Mock()
    auth.async_login = AsyncMock(return_value="token-fantasia")

    api = Mock()
    api.async_get_company_id = AsyncMock(return_value="id-company-fantasia")
    api.async_get_consumer = AsyncMock(return_value={"idConsumer": "id-consumer-fantasia"})
    api.async_get_pods = AsyncMock(
        return_value=[{"idPod": "id-pod-1", "code": "IT020E00000000001", "name": None}]
    )

    monkeypatch.setattr(ireti_auth, "IretiAuthClient", Mock(return_value=auth))
    monkeypatch.setattr(ireti_api, "IretiApiClient", Mock(return_value=api))
    monkeypatch.setattr(cf, "async_create_clientsession", lambda *a, **k: Mock())
    return SimpleNamespace(auth=auth, api=api)


async def _fino_a_ireti_user(hass):
    """user -> ... -> distributor_info -> submit, con ARERA che dà un
    operatore non supportato e scelta manuale di Ireti."""
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"regione": "Lombardia"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"provincia": "Milano"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"comune": "Vimodrone"})
    # manual_select -> scelgo ireti
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"distributor": "ireti"}
    )
    # distributor_info -> submit
    return await hass.config_entries.flow.async_configure(res["flow_id"], {})


async def test_ireti_credenziali_non_valide(hass, _arera_sconosciuto, ireti_mocks):
    ireti_mocks.auth.async_login.side_effect = IretiInvalidCredentials("credenziali errate")
    res = await _fino_a_ireti_user(hass)
    assert res["step_id"] == "ireti_user"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"username": "u", "password": "sbagliata"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "ireti_user"
    assert res["errors"] == {"base": "invalid_auth_username"}


async def test_ireti_un_solo_pod_crea_entry_subito(hass, _arera_sconosciuto, ireti_mocks):
    res = await _fino_a_ireti_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"username": "u", "password": "x"}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"]["distributor"] == "ireti"
    assert res["data"][IRETI_CONF_USERNAME] == "u"
    assert res["data"][IRETI_CONF_PASSWORD] == "x"
    assert res["data"][CONF_PODS] == ["IT020E00000000001"]


async def test_ireti_nessun_pod_associato_abortisce(hass, _arera_sconosciuto, ireti_mocks):
    """Lo stato di partenza di questa integrazione (issue #6): un account
    SmartPOD senza nessun POD associato - va segnalato chiaramente, non
    trattato come un errore generico."""
    ireti_mocks.api.async_get_pods.return_value = []
    res = await _fino_a_ireti_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"username": "u", "password": "x"}
    )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "no_ireti_pods_associated"


async def test_ireti_recupero_pod_fallito_abortisce(hass, _arera_sconosciuto, ireti_mocks):
    ireti_mocks.api.async_get_pods.side_effect = RuntimeError("boom")
    res = await _fino_a_ireti_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"username": "u", "password": "x"}
    )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "ireti_pods_failed"


async def test_ireti_piu_pod_si_scelgono(hass, _arera_sconosciuto, ireti_mocks):
    ireti_mocks.api.async_get_pods.return_value = [
        {"idPod": "id1", "code": "IT020E00000000001", "name": None},
        {"idPod": "id2", "code": "IT020E00000000002", "name": "Seconda casa"},
    ]
    res = await _fino_a_ireti_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"username": "u", "password": "x"}
    )
    assert res["step_id"] == "ireti_pod"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_PODS: ["IT020E00000000002"]}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_PODS] == ["IT020E00000000002"]


def _entry_ireti(hass, pods=("IT020E00000000001",)):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "ireti",
            IRETI_CONF_USERNAME: "u",
            IRETI_CONF_PASSWORD: "vecchia",
            CONF_PODS: list(pods),
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_ireti_reauth_aggiorna_credenziali(hass, ireti_mocks):
    entry = _entry_ireti(hass)
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert res["step_id"] == "ireti_reauth_user"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"username": "u", "password": "nuova"}
    )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "reauth_successful"
    assert entry.data[IRETI_CONF_PASSWORD] == "nuova"


async def test_ireti_reauth_credenziali_non_valide(hass, ireti_mocks):
    entry = _entry_ireti(hass)
    ireti_mocks.auth.async_login.side_effect = IretiInvalidCredentials("no")
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"username": "u", "password": "sbagliata"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"base": "invalid_auth_username"}


async def test_ireti_opzioni_non_supportate(hass):
    """v1 minimale: i POD si aggiornano solo tramite reauth, nessuna voce
    di opzioni dedicata ancora (vedi documentation/protocols/ireti-protocol.md)."""
    entry = _entry_ireti(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "options_not_supported"


# --- ramo SET Distribuzione (login B2C -> POD dall'account, come Ireti) ---

SET_POD_1 = {
    "pod": "IT999E00000001", "fiscal_code": "XXXX", "profile": "Retail_SET",
    "contract_id": "0100000001", "nome": "Casa",
}
SET_POD_2 = {**SET_POD_1, "pod": "IT999E00000002", "contract_id": "0100000002", "nome": ""}


@pytest.fixture
def set_mocks(monkeypatch):
    """Sostituisce SetDistribuzioneAuthClient / ApiClient e le sessioni
    aiohttp. Default: login ok, un solo POD sull'account."""
    auth = Mock()
    auth.async_login = AsyncMock(return_value="token-fantasia")

    api = Mock()
    api.async_get_pod_account = AsyncMock(return_value=[SET_POD_1])

    monkeypatch.setattr(set_auth, "SetDistribuzioneAuthClient", Mock(return_value=auth))
    monkeypatch.setattr(set_api, "SetDistribuzioneApiClient", Mock(return_value=api))
    monkeypatch.setattr(cf, "async_create_clientsession", lambda *a, **k: Mock())
    return SimpleNamespace(auth=auth, api=api)


async def _fino_a_set_user(hass):
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"regione": "Lombardia"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"provincia": "Milano"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"comune": "Vimodrone"})
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"distributor": "setdistribuzione"}
    )
    return await hass.config_entries.flow.async_configure(res["flow_id"], {})


async def test_set_credenziali_non_valide(hass, _arera_sconosciuto, set_mocks):
    set_mocks.auth.async_login.side_effect = SetDistribuzioneInvalidCredentials("no")
    res = await _fino_a_set_user(hass)
    assert res["step_id"] == "setdistribuzione_user"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "sbagliata"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"base": "invalid_auth_email"}


async def test_set_un_solo_pod_crea_entry_subito(hass, _arera_sconosciuto, set_mocks):
    res = await _fino_a_set_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["title"] == "SET Distribuzione (IT999E00000001)"
    assert res["data"]["distributor"] == "setdistribuzione"
    assert res["data"][SET_CONF_EMAIL] == "a@b.it"
    assert res["data"][SET_CONF_PASSWORD] == "x"
    assert res["data"][CONF_PODS] == ["IT999E00000001"]
    # Orario casuale per installazione, come E-Distribuzione.
    assert "18:00" <= res["options"]["orario_richiesta"] <= "22:00"


async def test_set_account_senza_forniture_abortisce(hass, _arera_sconosciuto, set_mocks):
    set_mocks.api.async_get_pod_account.return_value = []
    res = await _fino_a_set_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "no_setdistribuzione_pods"


async def test_set_recupero_pod_fallito_abortisce(hass, _arera_sconosciuto, set_mocks):
    set_mocks.api.async_get_pod_account.side_effect = RuntimeError("boom")
    res = await _fino_a_set_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "setdistribuzione_pods_failed"


async def test_set_piu_pod_si_scelgono(hass, _arera_sconosciuto, set_mocks):
    set_mocks.api.async_get_pod_account.return_value = [SET_POD_1, SET_POD_2]
    res = await _fino_a_set_user(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "x"}
    )
    assert res["step_id"] == "setdistribuzione_pod"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_PODS: ["IT999E00000002"]}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_PODS] == ["IT999E00000002"]


def _entry_set(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "distributor": "setdistribuzione",
            SET_CONF_EMAIL: "a@b.it",
            SET_CONF_PASSWORD: "vecchia",
            CONF_PODS: ["IT999E00000001"],
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_set_reauth_aggiorna_credenziali(hass, set_mocks):
    entry = _entry_set(hass)
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert res["step_id"] == "setdistribuzione_reauth_user"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "nuova"}
    )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "reauth_successful"
    assert entry.data[SET_CONF_PASSWORD] == "nuova"


async def test_set_reauth_credenziali_non_valide(hass, set_mocks):
    entry = _entry_set(hass)
    set_mocks.auth.async_login.side_effect = SetDistribuzioneInvalidCredentials("no")
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"email": "a@b.it", "password": "sbagliata"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"base": "invalid_auth_email"}


async def test_set_opzioni_non_supportate(hass):
    entry = _entry_set(hass)
    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "options_not_supported"


# --- accesso dal browser -----------------------------------------------------

REDIRECT_OK = (
    "Failed to launch 'eneldist://redirect#access_token=acc-browser"
    "&refresh_token=ref-browser&instance_url=https%3A%2F%2Fx&state=ST' because "
    "the scheme does not have a registered handler."
)


@pytest.fixture
def browser_mocks(edist_mocks):
    """Accesso dal browser: il client prepara il link e legge il redirect
    incollato; lo state del link e' "ST"."""
    auth = edist_mocks.auth
    auth.prepara_url_accesso_browser = Mock(return_value="https://link-di-accesso")

    def leggi(testo):
        if "state=ST" not in testo:
            raise edist_auth.EdistribuzioneRedirectNonValido("altro_tentativo")
        return SimpleNamespace(access_token="acc-browser", refresh_token="ref-browser")

    auth.token_da_redirect_browser = Mock(side_effect=leggi)
    return edist_mocks


async def _fino_a_edist_browser(hass):
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"regione": "Lombardia"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"provincia": "Milano"})
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"comune": "Vimodrone"})
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"distributor": "edistribuzione"}
    )
    return await hass.config_entries.flow.async_configure(
        res["flow_id"], {"modalita": "browser"}
    )


async def test_edist_browser_crea_entry_automatica(hass, _arera_sconosciuto, browser_mocks):
    browser_mocks_auth = browser_mocks.auth
    res = await _fino_a_edist_browser(hass)
    assert res["step_id"] == "edistribuzione_browser"
    assert res["description_placeholders"]["url"] == "https://link-di-accesso"
    # Prima il login sul portale, poi il link di autorizzazione.
    assert res["description_placeholders"]["login_url"] == edist_const.LOGIN_PAGE_URL

    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"redirect": REDIRECT_OK}
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_PODS] == ["IT001E00000009"]
    assert res["data"][CONF_REFRESH_TOKEN] == "ref-browser"
    browser_mocks.api.async_get_supplies.assert_awaited_once()
    browser_mocks_auth.token_da_redirect_browser.assert_called_once_with(REDIRECT_OK)


async def test_edist_browser_errore_ripresenta_lo_stesso_link(
    hass, _arera_sconosciuto, browser_mocks
):
    """Lo state del link deve restare quello che il redirect riporta: dopo
    un errore il form mostra lo stesso link, non uno nuovo."""
    res = await _fino_a_edist_browser(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"redirect": "eneldist://redirect#state=VECCHIO"}
    )
    assert res["step_id"] == "edistribuzione_browser"
    assert res["errors"] == {"base": "redirect_altro_tentativo"}
    assert res["description_placeholders"]["url"] == "https://link-di-accesso"
    browser_mocks.auth.prepara_url_accesso_browser.assert_called_once()


async def test_edist_browser_app_bloccata(hass, _arera_sconosciuto, browser_mocks):
    browser_mocks.auth.token_da_redirect_browser = Mock(
        side_effect=edist_auth.EdistribuzioneAppBloccata("blocked")
    )
    res = await _fino_a_edist_browser(hass)
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"redirect": REDIRECT_OK}
    )
    assert res["errors"] == {"base": "app_bloccata"}


async def test_edist_reauth_dal_browser_aggiorna_refresh_token(hass, browser_mocks):
    entry = _entry_edist(hass)
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"next_step_id": "edistribuzione_reauth_browser"}
    )
    assert res["step_id"] == "edistribuzione_reauth_browser"
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"redirect": REDIRECT_OK}
    )
    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "ref-browser"
    assert entry.data[CONF_PODS] == ["IT001E00000009"]


async def test_edist_reauth_dal_browser_con_altro_account(hass, browser_mocks):
    """Il link non forza un nuovo login: un browser collegato con un altro
    account darebbe i token di quell'account, che non vanno salvati."""
    browser_mocks.api.async_get_supplies = AsyncMock(
        return_value=[{"IdPod": "IT001E99999999"}]
    )
    entry = _entry_edist(hass)
    res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"next_step_id": "edistribuzione_reauth_browser"}
    )
    res = await hass.config_entries.flow.async_configure(
        res["flow_id"], {"redirect": REDIRECT_OK}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"] == {"base": "altro_account"}
    assert entry.data[CONF_REFRESH_TOKEN] == "ref-vecchio"
