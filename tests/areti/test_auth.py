"""Test per distributors/areti/auth.py: parsing HTML/regex e logica pura,
nessuna chiamata di rete reale.

auth.py di per se' non dipende da Home Assistant (solo aiohttp e libreria
standard), ma la gerarchia di pacchetti attorno si' (areti/__init__.py
importa homeassistant.config_entries, cosi' come distributors/__init__.py).
Per poterlo testare senza installare homeassistant, questo file carica
const.py e auth.py DIRETTAMENTE via importlib, bypassando quei __init__.py
- stesso trucco usato da tests/edistribuzione/test_auth.py e da
scripts/verify_areti_login.py.
"""
from __future__ import annotations

import importlib.util
import ssl
import sys
import types
from pathlib import Path

import aiohttp
import pytest

ARETI_DIR = (
    Path(__file__).parent.parent.parent
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "areti"
)


def _load_auth_module():
    pkg_name = "areti_test_auth"
    if f"{pkg_name}.auth" in sys.modules:
        return sys.modules[f"{pkg_name}.auth"]

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ARETI_DIR)]
    sys.modules[pkg_name] = pkg

    def _load(modname: str, filename: str):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{modname}", ARETI_DIR / filename
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{modname}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("const", "const.py")
    return _load("auth", "auth.py")


auth = _load_auth_module()


# ---------------------------------------------------------------------------
# _estrai_campo_hidden (campi ViewState della pagina di login Visualforce)
# ---------------------------------------------------------------------------


class TestEstraiCampoHidden:
    def test_trova_il_valore(self):
        html = '<input type="hidden" name="com.salesforce.visualforce.ViewState" value="ABC123" />'
        assert (
            auth._estrai_campo_hidden(html, "com.salesforce.visualforce.ViewState") == "ABC123"
        )

    def test_ordine_attributi_indifferente(self):
        """Il regex non deve assumere che 'value' segua 'name' con un
        ordine fisso di ALTRI attributi in mezzo (id, ecc.)."""
        html = '<input id="foo" type="hidden" name="MioCampo" class="x" value="XYZ" />'
        assert auth._estrai_campo_hidden(html, "MioCampo") == "XYZ"

    def test_campo_non_trovato_solleva_parsing_error(self):
        with pytest.raises(auth.AretiParsingError):
            auth._estrai_campo_hidden("<html>niente qui</html>", "CampoInesistente")


# ---------------------------------------------------------------------------
# _estrai_fwuid / _estrai_loaded_app_id / _estrai_nome_cookie_token
# (dal blob JSON inline della home loggata, /s/)
# ---------------------------------------------------------------------------


class TestEstraiFwuid:
    def test_trova_il_valore(self):
        html = '{"context":{"mode":"PROD","fwuid":"MzNzN1lSdDZQ..."}}'
        assert auth._estrai_fwuid(html) == "MzNzN1lSdDZQ..."

    def test_non_trovato_solleva_parsing_error(self):
        with pytest.raises(auth.AretiParsingError):
            auth._estrai_fwuid("<html>niente qui</html>")


class TestEstraiLoadedAppId:
    def test_trova_il_valore(self):
        html = '"loaded":{"APPLICATION@markup://siteforce:communityApp":"1712_xZHiuQoc1HHcvGz4vs6mGA"}'
        assert auth._estrai_loaded_app_id(html) == "1712_xZHiuQoc1HHcvGz4vs6mGA"

    def test_non_trovato_solleva_parsing_error(self):
        with pytest.raises(auth.AretiParsingError):
            auth._estrai_loaded_app_id("<html>niente qui</html>")


class TestEstraiNomeCookieToken:
    def test_trova_il_valore(self):
        """'eikoocnekot' e' 'tokencookie' letto al contrario - la chiave
        JSON offuscata trovata nella home reale il 04/09/2026: il suo
        VALORE e' il nome del cookie che porta aura.token."""
        html = '{"eikoocnekot":"__Host-ERIC_PROD-1963148453920734954","altro":1}'
        assert auth._estrai_nome_cookie_token(html) == "__Host-ERIC_PROD-1963148453920734954"

    def test_non_trovato_solleva_parsing_error(self):
        with pytest.raises(auth.AretiParsingError):
            auth._estrai_nome_cookie_token("<html>niente qui</html>")


# ---------------------------------------------------------------------------
# build_ssl_context (gotcha TLS: intermedio DigiCert mancante)
# ---------------------------------------------------------------------------


class TestBuildSslContext:
    def test_ritorna_un_contesto_ssl_valido(self):
        ctx = auth.build_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)

    def test_verifica_resta_attiva(self):
        """Il fix aggiunge un intermedio al bundle di CA, non deve MAI
        disabilitare la verifica del certificato - sarebbe un downgrade
        di sicurezza silenzioso."""
        ctx = auth.build_ssl_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True


# ---------------------------------------------------------------------------
# Costanti/dataclass
# ---------------------------------------------------------------------------


class TestAretiAuraContext:
    def test_costruzione(self):
        contesto = auth.AretiAuraContext(fwuid="fw", loaded_app_id="app", token="tok")
        assert contesto.fwuid == "fw"
        assert contesto.loaded_app_id == "app"
        assert contesto.token == "tok"


# ---------------------------------------------------------------------------
# _async_segui_ponti_login (redirect JS + "completa procedura di accesso",
# osservati per la prima volta il 18/09/2026 - non nella cattura originale
# del 04/09/2026 che ha fondato il modulo)
# ---------------------------------------------------------------------------


class _RispostaFake:
    def __init__(self, testo: str, url: str) -> None:
        self._testo = testo
        self.url = url

    def raise_for_status(self) -> None:
        pass

    async def text(self) -> str:
        return self._testo

    async def __aenter__(self) -> _RispostaFake:
        return self

    async def __aexit__(self, *args) -> bool:
        return False


class _SessioneFake:
    """Ritorna in sequenza le pagine configurate, una per GET."""

    def __init__(self, pagine: list[str]) -> None:
        self._pagine = list(pagine)
        self.richieste: list[str] = []

    def get(self, url: str, headers: dict | None = None) -> _RispostaFake:
        self.richieste.append(url)
        return _RispostaFake(self._pagine.pop(0), url=url)


_URL_INIZIALE = "https://areariservataclienti.areti.it/portaleareti/s/"
_PAGINA_VERA = '{"context":{"fwuid":"ABC123"}}'
_PAGINA_REDIRECT_JS = (
    "<script>window.location.replace("
    "'https://areariservataclienti.areti.it/portaleareti/loginflow/loginFlowOnly.apexp"
    "?retURL=%2Fportaleareti%2Fs%2F');</script>"
)
_PAGINA_REDIRECT_JS_HREF = (
    "<script>window.location.href = "
    "'https://areariservataclienti.areti.it/portaleareti/loginflow/loginFlowOnly.apexp"
    "?retURL=%2Fportaleareti%2Fs%2F';</script>"
)
# Il '&amp;' e' l'HTML entity reale osservata nella cattura del 18/09/2026,
# non un '&' semplice - va decodificato prima della richiesta.
_PAGINA_COMPLETA_PROCEDURA = (
    '<title>Impossibile visualizzare la pagina</title>'
    '<a href="https://areariservataclienti.areti.it/portaleareti/loginflow/loginFlow.apexp'
    '?retURL=%2Fportaleareti%2Fs%2F&amp;sparkID=ARIA_MaintenanceFlow" '
    'class="button primary wide mt16">Completa procedura di accesso</a>'
)


class TestAsyncSeguiPontiLogin:
    @pytest.mark.asyncio
    async def test_nessun_ponte_ritorna_html_invariato(self):
        """Caso comune (cattura originale del 04/09/2026): la home carica
        subito, nessuna richiesta aggiuntiva."""
        client = auth.AretiAuthClient(_SessioneFake([]))
        risultato = await client._async_segui_ponti_login({}, _PAGINA_VERA, _URL_INIZIALE)
        assert risultato == _PAGINA_VERA

    @pytest.mark.asyncio
    async def test_segue_redirect_js_replace(self):
        sessione = _SessioneFake([_PAGINA_VERA])
        client = auth.AretiAuthClient(sessione)
        risultato = await client._async_segui_ponti_login(
            {}, _PAGINA_REDIRECT_JS, _URL_INIZIALE
        )
        assert risultato == _PAGINA_VERA
        assert sessione.richieste == [
            "https://areariservataclienti.areti.it/portaleareti/loginflow/loginFlowOnly.apexp"
            "?retURL=%2Fportaleareti%2Fs%2F"
        ]

    @pytest.mark.asyncio
    async def test_segue_redirect_js_href(self):
        sessione = _SessioneFake([_PAGINA_VERA])
        client = auth.AretiAuthClient(sessione)
        risultato = await client._async_segui_ponti_login(
            {}, _PAGINA_REDIRECT_JS_HREF, _URL_INIZIALE
        )
        assert risultato == _PAGINA_VERA

    @pytest.mark.asyncio
    async def test_segue_link_completa_procedura_e_decodifica_amp(self):
        sessione = _SessioneFake([_PAGINA_VERA])
        client = auth.AretiAuthClient(sessione)
        risultato = await client._async_segui_ponti_login(
            {}, _PAGINA_COMPLETA_PROCEDURA, _URL_INIZIALE
        )
        assert risultato == _PAGINA_VERA
        # '&amp;' deve diventare '&' prima della richiesta, non restare
        # letterale nell'URL.
        assert sessione.richieste == [
            "https://areariservataclienti.areti.it/portaleareti/loginflow/loginFlow.apexp"
            "?retURL=%2Fportaleareti%2Fs%2F&sparkID=ARIA_MaintenanceFlow"
        ]

    @pytest.mark.asyncio
    async def test_segue_entrambi_i_ponti_in_sequenza(self):
        """La catena osservata per davvero il 18/09/2026: redirect JS poi
        link 'completa procedura', in due hop distinti."""
        sessione = _SessioneFake([_PAGINA_COMPLETA_PROCEDURA, _PAGINA_VERA])
        client = auth.AretiAuthClient(sessione)
        risultato = await client._async_segui_ponti_login(
            {}, _PAGINA_REDIRECT_JS, _URL_INIZIALE
        )
        assert risultato == _PAGINA_VERA
        assert len(sessione.richieste) == 2

    @pytest.mark.asyncio
    async def test_max_hop_si_ferma_senza_sollevare(self):
        """Una catena di redirect che non finisce mai (bug lato server, o
        pattern non riconosciuto) si ferma dopo max_hop invece di andare
        in loop infinito - ritorna l'ultima pagina vista, il chiamante
        (_estrai_fwuid) sollevera' un errore chiaro su quella."""
        pagina_redirect_a_se_stessa = _PAGINA_REDIRECT_JS
        sessione = _SessioneFake([pagina_redirect_a_se_stessa] * 10)
        client = auth.AretiAuthClient(sessione)
        risultato = await client._async_segui_ponti_login(
            {}, _PAGINA_REDIRECT_JS, _URL_INIZIALE, max_hop=3
        )
        assert risultato == pagina_redirect_a_se_stessa
        assert len(sessione.richieste) == 3


# ---------------------------------------------------------------------------
# async_create_session
#
# Regressione: osservato in produzione il 18/09/2026 che
# homeassistant.helpers.aiohttp_client.async_create_clientsession(hass,
# connector=...) solleva "TypeError: got multiple values for keyword
# argument 'connector'" - quell'helper costruisce sempre il proprio
# connector internamente e non accetta un connector personalizzato.
# async_create_session costruisce la sessione a mano per questo.
# ---------------------------------------------------------------------------


class _BusFake:
    def __init__(self) -> None:
        self.listener_registrato: tuple[str, object] | None = None

    def async_listen_once(self, evento: str, callback) -> None:
        self.listener_registrato = (evento, callback)


class _HassFake:
    """Solo cio' che async_create_session usa davvero: async_add_executor_job
    (per l'SSLContext, che fa I/O bloccante - vedi build_ssl_context) e
    bus.async_listen_once (per chiudere la sessione all'arresto di HA)."""

    def __init__(self) -> None:
        self.bus = _BusFake()
        self.chiamate_executor: list[object] = []

    async def async_add_executor_job(self, func):
        self.chiamate_executor.append(func)
        return func()


class TestAsyncCreateSession:
    @pytest.mark.asyncio
    async def test_costruisce_una_sessione_aiohttp_vera(self):
        hass = _HassFake()
        session = await auth.async_create_session(hass)
        try:
            assert isinstance(session, aiohttp.ClientSession)
            assert not session.closed
        finally:
            await session.close()

    @pytest.mark.asyncio
    async def test_ssl_context_e_costruito_in_un_executor(self):
        """build_ssl_context() fa I/O bloccante (legge i certificati di
        sistema) - deve passare da async_add_executor_job, mai essere
        chiamato direttamente nell'event loop (osservato in produzione il
        18/09/2026 come 'blocking call' quando non lo era)."""
        hass = _HassFake()
        session = await auth.async_create_session(hass)
        try:
            assert auth.build_ssl_context in hass.chiamate_executor
        finally:
            await session.close()

    @pytest.mark.asyncio
    async def test_registra_un_listener_per_chiudersi_allo_spegnimento(self):
        hass = _HassFake()
        session = await auth.async_create_session(hass)
        try:
            assert hass.bus.listener_registrato is not None
            evento, _callback = hass.bus.listener_registrato
            assert evento == "homeassistant_stop"
        finally:
            await session.close()

    @pytest.mark.asyncio
    async def test_il_listener_chiude_davvero_la_sessione(self):
        hass = _HassFake()
        session = await auth.async_create_session(hass)
        assert not session.closed
        _evento, callback = hass.bus.listener_registrato
        await callback(None)
        assert session.closed
