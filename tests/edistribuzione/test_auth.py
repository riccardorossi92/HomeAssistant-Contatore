"""Test per distributors/edistribuzione/auth.py: parsing HTML/regex e
logica pura, nessuna chiamata di rete.

auth.py di per se' non dipende da Home Assistant (solo aiohttp e libreria
standard), ma la gerarchia di pacchetti attorno si' (edistribuzione/__init__.py
importa homeassistant.config_entries, così come distributors/__init__.py).
Per poterlo testare senza installare homeassistant, questo file carica
const.py e auth.py DIRETTAMENTE via importlib, bypassando quei __init__.py
- esattamente lo stesso trucco usato da scripts/test_edistribuzione_login.py,
qui riproposto in forma di test automatizzati.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

EDISTRIBUZIONE_DIR = (
    Path(__file__).parent.parent.parent
    / "custom_components"
    / "contatore_letture"
    / "distributors"
    / "edistribuzione"
)


def _load_auth_module():
    pkg_name = "edistribuzione_test"
    if f"{pkg_name}.auth" in sys.modules:
        return sys.modules[f"{pkg_name}.auth"]

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(EDISTRIBUZIONE_DIR)]
    sys.modules[pkg_name] = pkg

    def _load(modname: str, filename: str):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{modname}", EDISTRIBUZIONE_DIR / filename
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{modname}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("const", "const.py")
    return _load("auth", "auth.py")


auth = _load_auth_module()


# ---------------------------------------------------------------------------
# _extract_fwuid
# ---------------------------------------------------------------------------


class TestExtractFwuid:
    def test_forma_diretta(self):
        html = '<script>var ctx = {"mode":"PROD","fwuid":"ABC123"};</script>'
        assert auth.EdistribuzioneAuthClient._extract_fwuid(html) == "ABC123"

    def test_forma_percent_encoded(self):
        """Confermato su una cattura reale il 20/08/2026: il fwuid è
        incorporato percent-encoded nel path di un URL di bootstrap, non
        in una variabile JS in chiaro."""
        html = (
            '<script src="/PortaleClienti/s/sfsites/l/%7B%22mode%22%3A%22PROD%22'
            '%2C%22fwuid%22%3A%22XYZ789%22%2C%22loaded%22%3A%7B%7D%7D/app.js">'
            "</script>"
        )
        assert auth.EdistribuzioneAuthClient._extract_fwuid(html) == "XYZ789"

    def test_non_trovato_solleva_errore(self):
        with pytest.raises(auth.EdistribuzioneParsingError):
            auth.EdistribuzioneAuthClient._extract_fwuid("<html>niente qui</html>")


# ---------------------------------------------------------------------------
# _extract_loaded
# ---------------------------------------------------------------------------


class TestExtractLoaded:
    def test_forma_diretta(self):
        html = '{"loaded":{"APPLICATION@markup://siteforce:loginApp2":"123"}}'
        assert (
            auth.EdistribuzioneAuthClient._extract_loaded(html)
            == '{"APPLICATION@markup://siteforce:loginApp2":"123"}'
        )

    def test_forma_percent_encoded(self):
        """Stesso blob percent-encoded di _extract_fwuid: 'loaded' contiene
        un riferimento alla versione del componente caricato, necessario
        perché aura.context con 'loaded':{} vuoto viene rifiutato dal
        server (AuraClientInputException, risolto in v0.1.0)."""
        html = (
            "%22loaded%22%3A%7B%22APPLICATION%40markup%3A%2F%2Fsiteforce"
            "%3AloginApp2%22%3A%221628_TW-5Qu0N5YI_dQXZ9Cqalw%22%7D"
        )
        risultato = auth.EdistribuzioneAuthClient._extract_loaded(html)
        assert risultato == (
            '{"APPLICATION@markup://siteforce:loginApp2":"1628_TW-5Qu0N5YI_dQXZ9Cqalw"}'
        )

    def test_fallback_oggetto_vuoto_se_non_trovato(self):
        """Non solleva errore: degrada a '{}' e lascia che sia il server a
        segnalare il problema (già diagnosticato altrove)."""
        assert auth.EdistribuzioneAuthClient._extract_loaded("<html>niente</html>") == "{}"


# ---------------------------------------------------------------------------
# _extract_js_redirect_url
# ---------------------------------------------------------------------------


class TestExtractJsRedirectUrl:
    def test_window_location_replace(self):
        html = (
            "<script>window.location.replace"
            "('https://example.com/target?a=1&b=2');</script>"
        )
        assert (
            auth.EdistribuzioneAuthClient._extract_js_redirect_url(html)
            == "https://example.com/target?a=1&b=2"
        )

    def test_window_location_href_variante(self):
        html = "<script>window.location.href = 'https://example.com/other';</script>"
        assert (
            auth.EdistribuzioneAuthClient._extract_js_redirect_url(html)
            == "https://example.com/other"
        )

    def test_non_trovato_solleva_errore(self):
        with pytest.raises(auth.EdistribuzioneParsingError):
            auth.EdistribuzioneAuthClient._extract_js_redirect_url("<html>niente qui</html>")


# ---------------------------------------------------------------------------
# _parse_otp_page
# ---------------------------------------------------------------------------


class TestParseOtpPage:
    """Il gruppo più importante: copre in particolare la regressione del
    campo Richiedi_nuovo_OTP (checkbox vs hidden) risolta in v0.1.3, che
    ha richiesto diversi round di debug con dati reali per essere trovata."""

    HTML_PAGINA_OTP = """
    <html><body>
      <input type="hidden" name="com.salesforce.visualforce.ViewState" value="VS123" />
      <input type="hidden" name="com.salesforce.visualforce.ViewStateVersion" value="VSV456" />
      <input type="hidden" name="com.salesforce.visualforce.ViewStateMAC" value="VSM789" />
      <input type="hidden" name="com.salesforce.visualforce.ViewStateCSRF" value="VSC000" />
      <input id="thePage:j_id2:i:f:pb:d:OTP_Input.input"
             name="thePage:j_id2:i:f:pb:d:element___input____OTP_Input"
             type="text" value="" />
      <input id="thePage:j_id2:i:f:pb:d:Richiedi_nuovo_OTP.input"
             name="thePage:j_id2:i:f:pb:d:element___input____Richiedi_nuovo_OTP"
             type="checkbox" value="true" />
      <input type="hidden"
             id="thePage:j_id2:i:f:pb:d:element___hidden____Richiedi_nuovo_OTP"
             name="thePage:j_id2:i:f:pb:d:element___hidden____Richiedi_nuovo_OTP"
             value="false" />
    </body></html>
    """

    @staticmethod
    def _client():
        return auth.EdistribuzioneAuthClient(session=None)

    def test_estrae_tutti_i_campi_viewstate(self):
        client = self._client()
        client._parse_otp_page(self.HTML_PAGINA_OTP)
        assert client._flow.view_state == "VS123"
        assert client._flow.view_state_version == "VSV456"
        assert client._flow.view_state_mac == "VSM789"
        assert client._flow.view_state_csrf == "VSC000"

    def test_resend_field_e_il_campo_hidden_non_la_checkbox(self):
        """Prima del fix v0.1.3, l'estrazione prendeva la checkbox visibile
        (element___input____Richiedi_nuovo_OTP) invece del campo nascosto,
        causando l'invio del campo sbagliato nella sottomissione dell'OTP:
        il server trattava ogni submit come un'ennesima richiesta di
        reinvio invece che una convalida del codice."""
        client = self._client()
        client._parse_otp_page(self.HTML_PAGINA_OTP)
        assert client._flow.resend_field_name == (
            "thePage:j_id2:i:f:pb:d:element___hidden____Richiedi_nuovo_OTP"
        )
        assert "input____Richiedi_nuovo_OTP" not in client._flow.resend_field_name

    def test_ordine_attributi_invertito_nel_tag(self):
        """Il vecchio regex assumeva 'name' sempre prima di 'value' nel
        tag: se l'ordine è invertito (value poi name), deve funzionare
        comunque - risolto assieme al fix del campo hidden/checkbox."""
        html_invertito = (
            '<input type="hidden" value="VS_INVERTITO" '
            'name="com.salesforce.visualforce.ViewState" />'
        )
        client = self._client()
        # Solo il primo campo è presente: l'estrazione del secondo fallirà,
        # ma vogliamo verificare che il PRIMO sia stato letto correttamente
        # nonostante l'ordine invertito degli attributi.
        with pytest.raises(auth.EdistribuzioneParsingError):
            client._parse_otp_page(html_invertito)
        assert client._flow.view_state == "VS_INVERTITO"

    def test_campo_mancante_solleva_errore_con_nome_campo(self):
        client = self._client()
        with pytest.raises(auth.EdistribuzioneParsingError, match="ViewState"):
            client._parse_otp_page("<html><title>Sessione scaduta</title></html>")


# ---------------------------------------------------------------------------
# Gerarchia eccezioni
# ---------------------------------------------------------------------------


class TestGerarchiaEccezioni:
    def test_invalid_credentials_e_sottoclasse_di_auth_error(self):
        assert issubclass(auth.EdistribuzioneInvalidCredentials, auth.EdistribuzioneAuthError)

    def test_invalid_otp_e_sottoclasse_di_auth_error(self):
        assert issubclass(auth.EdistribuzioneInvalidOtp, auth.EdistribuzioneAuthError)

    def test_parsing_error_e_sottoclasse_di_auth_error(self):
        assert issubclass(auth.EdistribuzioneParsingError, auth.EdistribuzioneAuthError)
