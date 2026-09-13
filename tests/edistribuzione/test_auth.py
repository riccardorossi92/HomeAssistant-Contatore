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

import asyncio
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

    def test_cattura_anche_la_checkbox_di_reinvio(self):
        """La checkbox visibile serve per il reinvio esplicito del codice
        (async_resend_otp), dove riproduciamo il submit di un browser con la
        casella spuntata - il campo hidden da solo non basta a rappresentarlo."""
        client = self._client()
        client._parse_otp_page(self.HTML_PAGINA_OTP)
        assert client._flow.resend_checkbox_field_name == (
            "thePage:j_id2:i:f:pb:d:element___input____Richiedi_nuovo_OTP"
        )


# ---------------------------------------------------------------------------
# _dati_form_otp (payload dei tre submit del form)
# ---------------------------------------------------------------------------


class TestDatiFormOtp:
    @staticmethod
    def _client():
        client = auth.EdistribuzioneAuthClient(session=None)
        client._parse_otp_page(TestParseOtpPage.HTML_PAGINA_OTP)
        return client

    def test_invio_iniziale_senza_codice_ne_flag_di_reinvio(self):
        """Il primo submit serve solo a far spedire il codice: non deve
        contenere ne' un OTP da convalidare ne' il flag di reinvio."""
        dati = self._client()._dati_form_otp()
        assert not [k for k in dati if "OTP_Input" in k]
        assert not [k for k in dati if "Richiedi_nuovo_OTP" in k]
        assert dati["com.salesforce.visualforce.ViewState"] == "VS123"

    def test_convalida_codice_manda_hidden_a_false(self):
        """Regressione v0.1.3: con il flag di reinvio a 'true' il server
        trattava la convalida come un'ennesima richiesta di nuovo codice."""
        dati = self._client()._dati_form_otp(otp_code="12345")
        assert dati["thePage:j_id2:i:f:pb:d:element___input____OTP_Input"] == "12345"
        assert (
            dati["thePage:j_id2:i:f:pb:d:element___hidden____Richiedi_nuovo_OTP"]
            == "false"
        )

    def test_reinvio_manda_hidden_e_checkbox_a_true_senza_codice(self):
        dati = self._client()._dati_form_otp(richiedi_nuovo=True)
        assert (
            dati["thePage:j_id2:i:f:pb:d:element___hidden____Richiedi_nuovo_OTP"]
            == "true"
        )
        assert (
            dati["thePage:j_id2:i:f:pb:d:element___input____Richiedi_nuovo_OTP"]
            == "true"
        )
        assert not [k for k in dati if "OTP_Input" in k]


# ---------------------------------------------------------------------------
# Invio / reinvio del codice: conferma e limite di sessioni (issue #2)
# ---------------------------------------------------------------------------


class _RispostaFinta:
    def __init__(self, body: str) -> None:
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False


class _SessioneFinta:
    """Minimo indispensabile per _async_post_form_otp: registra i dati
    inviati e restituisce sempre la stessa pagina."""

    def __init__(self, body: str) -> None:
        self._body = body
        self.dati_inviati: dict | None = None

    def post(self, url, data=None, headers=None):
        self.dati_inviati = data
        return _RispostaFinta(self._body)


PAGINA_OTP_CON_CONFERMA = (
    "<html><body><p>Abbiamo inviato un codice a 5 cifre al tuo indirizzo "
    "email</p>" + TestParseOtpPage.HTML_PAGINA_OTP + "</body></html>"
)


def _client_su_form_otp(body: str):
    session = _SessioneFinta(body)
    client = auth.EdistribuzioneAuthClient(session)
    client._parse_otp_page(TestParseOtpPage.HTML_PAGINA_OTP)
    return client, session


class TestInvioOtp:
    async def test_conferma_invio_riconosciuta(self):
        client, _ = _client_su_form_otp(PAGINA_OTP_CON_CONFERMA)
        await client._async_trigger_otp_send()
        assert client.otp_invio_confermato is True

    async def test_invio_non_confermato_non_e_fatale_ma_viene_segnalato(
        self, caplog, monkeypatch, tmp_path
    ):
        """Se la pagina non contiene nessuno dei messaggi di conferma noti il
        flusso continua (la formulazione potrebbe essere solo diversa), ma
        resta traccia nei log e il flag permette al config flow di avvisare:
        il sintomo dell'issue #2 era proprio un OTP mai spedito e nessun
        indizio da nessuna parte."""
        # Il dump di debug viene scritto nella cwd: lo dirottiamo su tmp_path
        # per non lasciare otp_send_debug.html dentro il repository.
        monkeypatch.chdir(tmp_path)
        client, _ = _client_su_form_otp(TestParseOtpPage.HTML_PAGINA_OTP)
        await client._async_trigger_otp_send()
        assert client.otp_invio_confermato is False
        assert "non ha confermato l'invio" in caplog.text
        # Il dump e' scritto in un executor (per non bloccare l'event loop di
        # Home Assistant), quindi puo' non esistere ancora al ritorno della
        # chiamata.
        dump = tmp_path / "otp_send_debug.html"
        for _ in range(50):
            if dump.exists():
                break
            await asyncio.sleep(0.02)
        assert dump.exists()

    async def test_pagina_limite_sessioni_solleva_eccezione_dedicata(
        self, monkeypatch, tmp_path
    ):
        """Con troppe sessioni aperte nessun codice viene spedito: va
        distinto da un OTP sbagliato o da un cambio di markup, altrimenti
        l'utente resta ad aspettare un codice che non arrivera' mai."""
        monkeypatch.chdir(tmp_path)
        client, _ = _client_su_form_otp(
            "<html><body>Hai superato il numero di sessioni simultanee "
            "consentite</body></html>"
        )
        with pytest.raises(auth.EdistribuzioneTroppeSessioni):
            await client._async_trigger_otp_send()

    async def test_resend_otp_invia_il_flag_di_reinvio(self):
        client, session = _client_su_form_otp(PAGINA_OTP_CON_CONFERMA)
        assert await client.async_resend_otp() is True
        assert (
            session.dati_inviati[
                "thePage:j_id2:i:f:pb:d:element___hidden____Richiedi_nuovo_OTP"
            ]
            == "true"
        )

    async def test_resend_otp_prima_del_login_e_un_errore(self):
        client = auth.EdistribuzioneAuthClient(session=None)
        with pytest.raises(auth.EdistribuzioneAuthError, match="async_begin_login"):
            await client.async_resend_otp()


# ---------------------------------------------------------------------------
# Schermata di consenso OAuth (issue #2)
# ---------------------------------------------------------------------------


# Struttura ricalcata sulla pagina reale catturata il 13/09/2026
# (consent_page_debug.html, 9860 caratteri): titolo "Consentire l'accesso?",
# un solo form con 8 campi hidden e DUE pulsanti submit con lo stesso
# name="save", distinguibili solo per il valore - "Consenti" approva,
# " Nega " (con gli spazi) rifiuta.
HTML_PAGINA_CONSENSO = """
<html><head><title>Consentire l'accesso? | Portale Clienti</title></head>
<body>
  <form id="editPage" method="post"
        action="/PortaleClienti/_ui/identity/oauth/ui/AuthorizationPage">
    <input type="hidden" name="_CONFIRMATIONTOKEN" value="TOKEN123" />
    <input type="hidden" name="cancelURL" value="/PortaleClienti/home/home.jsp" />
    <input type="hidden" name="retURL" value="/PortaleClienti/home/home.jsp" />
    <input type="hidden" name="save_new_url"
           value="/services/oauth2/approval?a=1&amp;b=2&amp;c=l&#39;app" />
    <input type="hidden" name="source" value="SORGENTE456" />
    <input type="hidden" name="scope_hint" value="web api openid" />
    <input type="hidden" name="authPageHint" value="HINT789" />
    <input type="hidden" name="display" value="touch" />
    <input type="submit" name="save" value="Consenti" class="button primary" />
    <input type="submit" name="save" value=" Nega " id="oadeny" />
  </form>
</body></html>
"""


class TestFormConsenso:
    def test_seleziona_consenti_e_non_nega(self):
        """I due pulsanti hanno lo stesso name: prendere quello sbagliato
        significherebbe NEGARE l'autorizzazione all'integrazione."""
        action, dati = auth.EdistribuzioneAuthClient._estrai_form_consenso(
            HTML_PAGINA_CONSENSO
        )
        assert dati["save"] == "Consenti"
        assert (
            action
            == "https://private.e-distribuzione.it/PortaleClienti/_ui/identity/oauth/ui/AuthorizationPage"
        )

    def test_rimanda_tutti_i_campi_hidden_deescapati(self):
        """save_new_url e source sono URL pieni di parametri: se restano
        escapati (&amp;) il server riceve valori diversi da quelli che ha
        generato."""
        _, dati = auth.EdistribuzioneAuthClient._estrai_form_consenso(
            HTML_PAGINA_CONSENSO
        )
        assert set(dati) == {
            "_CONFIRMATIONTOKEN",
            "cancelURL",
            "retURL",
            "save_new_url",
            "source",
            "scope_hint",
            "authPageHint",
            "display",
            "save",
        }
        assert dati["save_new_url"] == "/services/oauth2/approval?a=1&b=2&c=l'app"

    def test_pagina_senza_pulsante_di_consenso_ritorna_none(self):
        """Su una pagina che non e' una schermata di consenso il chiamante
        deve poter proseguire col suo errore di parsing abituale."""
        assert (
            auth.EdistribuzioneAuthClient._estrai_form_consenso(
                "<html><body><form><input type='submit' name='x' value='Invia'>"
                "</form></body></html>"
            )
            is None
        )


class _RispostaConLocation(_RispostaFinta):
    def __init__(self, body: str, location: str | None = None) -> None:
        super().__init__(body)
        self.headers = {"Location": location} if location else {}


class _SessioneACoda:
    """Restituisce le risposte in coda, una per chiamata (get o post), e
    registra i payload inviati."""

    def __init__(self, risposte: list) -> None:
        self._risposte = list(risposte)
        self.post_inviati: list[dict] = []

    def _prossima(self):
        return self._risposte.pop(0)

    def post(self, url, data=None, headers=None, allow_redirects=None):
        self.post_inviati.append({"url": url, "data": data})
        return self._prossima()

    def get(self, url, headers=None):
        return self._prossima()


class TestApprovazioneConsenso:
    async def test_preme_consenti_e_recupera_il_codice(self):
        """Il percorso completo dell'issue #2: dopo l'OTP arriva la
        schermata di consenso invece del codice; premendo "Consenti" il
        codice arriva nel Location del redirect verso lo schema custom
        dell'app (eneldist://), che aiohttp non puo' seguire."""
        session = _SessioneACoda([
            _RispostaConLocation(
                "eneldist://redirect?code=CODICE_OK&state=S",
                location="eneldist://redirect?code=CODICE_OK&state=S",
            )
        ])
        client = auth.EdistribuzioneAuthClient(session)
        risultato = await client._async_approva_consenso(HTML_PAGINA_CONSENSO)
        assert "code=CODICE_OK" in risultato
        assert session.post_inviati[0]["data"]["save"] == "Consenti"
        assert (
            session.post_inviati[0]["url"].endswith("/AuthorizationPage")
        )

    async def test_senza_form_di_consenso_ritorna_none(self):
        client = auth.EdistribuzioneAuthClient(_SessioneACoda([]))
        assert await client._async_approva_consenso("<html>niente</html>") is None


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

    def test_troppe_sessioni_e_sottoclasse_di_auth_error_ma_non_di_credenziali(self):
        """Deve essere gestibile a parte: le credenziali sono corrette, e'
        l'account ad avere troppe sessioni aperte."""
        assert issubclass(auth.EdistribuzioneTroppeSessioni, auth.EdistribuzioneAuthError)
        assert not issubclass(
            auth.EdistribuzioneTroppeSessioni, auth.EdistribuzioneInvalidCredentials
        )
