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
import json
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

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


class _RispostaLoginFinta:
    def __init__(self, status: int, url: str, body: str) -> None:
        from yarl import URL

        self.status = status
        self.url = URL(url)
        self._body = body

    async def text(self) -> str:
        return self._body

    def close(self) -> None:
        pass


class _RispostaLoginUser:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False


class _SessioneLoginUser:
    def __init__(self, risposta: _RispostaLoginUser) -> None:
        self._risposta = risposta

    def post(self, url, data=None, headers=None):
        return self._risposta


class TestLoginRifiutato:
    PAGINA_LOGIN = (
        '<script>var ctx = {"mode":"PROD","fwuid":"ABC123",'
        '"loaded":{"APPLICATION@markup://siteforce:loginApp2":"1"}};</script>'
    )

    async def _rifiuta(
        self, monkeypatch, body: str, eccezione=auth.EdistribuzioneInvalidCredentials
    ):
        risposta_login = _RispostaLoginFinta(
            200,
            "https://private.e-distribuzione.it/PortaleClienti/s/login/"
            "?startURL=%2Fx",
            self.PAGINA_LOGIN,
        )

        async def _finto_get(*args, **kwargs):
            return risposta_login

        monkeypatch.setattr(auth, "_get_following_redirects", _finto_get)
        client = auth.EdistribuzioneAuthClient(
            _SessioneLoginUser(_RispostaLoginUser(200, body))
        )
        with pytest.raises(eccezione) as exc_info:
            await client.async_begin_login("Mario@Example.com", "segreta")
        return exc_info.value

    async def _dump(self, tmp_path):
        # Scritto in un executor, come gli altri dump di debug.
        dump = tmp_path / "login_response_debug.json"
        for _ in range(50):
            if dump.exists() and dump.stat().st_size:
                break
            await asyncio.sleep(0.02)
        return dump.read_text(encoding="utf-8")

    async def test_warning_con_host_e_motivo_e_dump_senza_email(
        self, monkeypatch, tmp_path, caplog
    ):
        """Credenziali che funzionano sul sito ma rifiutate qui: il log deve
        dire su quale host e con quale messaggio, senza l'email."""
        monkeypatch.chdir(tmp_path)
        body = json.dumps({
            "actions": [{
                "state": "SUCCESS",
                "returnValue": "Credenziali errate per mario%40example.com",
            }]
        })
        errore = await self._rifiuta(monkeypatch, body)

        avvisi = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "Login E-Distribuzione rifiutato su private.e-distribuzione.it (HTTP 200)"
            in r.getMessage()
            for r in avvisi
        )
        assert "Credenziali errate per ***" in caplog.text
        assert "mario" not in caplog.text.lower()
        assert "mario" not in str(errore).lower()
        dump = await self._dump(tmp_path)
        assert "***" in dump and "mario" not in dump.lower()

    async def test_errore_tecnico_ha_eccezione_dedicata_e_niente_dump(
        self, monkeypatch, tmp_path, caplog
    ):
        """"KO: Errore Tecnico" non dice che le credenziali sono sbagliate:
        non deve finire nel ramo delle credenziali errate. E' un guasto noto
        del portale, quindi basta il warning: niente dump ne' riga ERROR."""
        monkeypatch.chdir(tmp_path)
        body = json.dumps({
            "actions": [{
                "state": "SUCCESS",
                "returnValue": "KO: Errore Tecnico",
            }]
        })
        errore = await self._rifiuta(
            monkeypatch, body, eccezione=auth.EdistribuzioneErroreTecnico
        )
        assert not isinstance(errore, auth.EdistribuzioneInvalidCredentials)
        assert "KO: Errore Tecnico" in caplog.text
        assert not any(r.levelname == "ERROR" for r in caplog.records)
        await asyncio.sleep(0.1)
        assert not (tmp_path / "login_response_debug.json").exists()

    async def test_azione_in_errore_riporta_il_messaggio_del_server(
        self, monkeypatch, tmp_path, caplog
    ):
        monkeypatch.chdir(tmp_path)
        body = json.dumps({
            "actions": [{
                "state": "ERROR",
                "returnValue": None,
                "error": [{"message": "Utente bloccato"}],
            }]
        })
        await self._rifiuta(monkeypatch, body)
        assert "Utente bloccato" in caplog.text


class TestBeginLoginSenzaFwuid:
    async def test_errore_riporta_status_e_url_senza_email(
        self, monkeypatch, tmp_path, caplog
    ):
        """Con il portale in manutenzione arriva una pagina senza fwuid: il
        messaggio deve bastare a distinguerlo da un cambio di markup, senza
        far finire nei log l'email contenuta in login_hint."""
        monkeypatch.chdir(tmp_path)
        risposta = _RispostaLoginFinta(
            503,
            "https://private.e-distribuzione.it/PortaleClienti/s/login/"
            "?login_hint=mario%40example.com&startURL=%2Fx",
            "<html><head><title>Servizio non disponibile</title></head></html>",
        )

        async def _finto_get(*args, **kwargs):
            return risposta

        monkeypatch.setattr(auth, "_get_following_redirects", _finto_get)
        client = auth.EdistribuzioneAuthClient(session=None)
        with pytest.raises(auth.EdistribuzioneParsingError) as exc_info:
            await client.async_begin_login("mario@example.com", "pw")

        messaggio = str(exc_info.value)
        assert "HTTP 503" in messaggio
        assert "/PortaleClienti/s/login/" in messaggio
        assert "mario" not in messaggio
        assert "Servizio non disponibile" in caplog.text

    async def test_pagina_antibot_solleva_eccezione_dedicata(
        self, monkeypatch, tmp_path
    ):
        """Visto in un log reale l'08/10/2026: HTTP 200 su oauth2/authorize
        con la pagina di blocco Imperva al posto del login."""
        monkeypatch.chdir(tmp_path)
        risposta = _RispostaLoginFinta(
            200,
            "https://private.e-distribuzione.it/PortaleClienti/services/oauth2/"
            "authorize?login_hint=mario%40example.com",
            "<!DOCTYPE html>\r\n<html>\r\n<head>\r\n <noscript>\r\n"
            " <title>Pardon Our Interruption</title>\r\n </noscript>",
        )

        async def _finto_get(*args, **kwargs):
            return risposta

        monkeypatch.setattr(auth, "_get_following_redirects", _finto_get)
        client = auth.EdistribuzioneAuthClient(session=None)
        with pytest.raises(auth.EdistribuzioneBloccoAntibot) as exc_info:
            await client.async_begin_login("mario@example.com", "pw")
        assert not isinstance(exc_info.value, auth.EdistribuzioneParsingError)
        assert "mario" not in str(exc_info.value)

    async def test_pagina_di_debug_salvata_solo_per_il_blocco_sul_diretto(
        self, monkeypatch, tmp_path
    ):
        """Il blocco sull'host principale e' atteso (si ripiega sul diretto):
        niente pagina di debug, solo per il fallimento vero."""
        monkeypatch.chdir(tmp_path)
        pagina = "<title>Pardon Our Interruption</title>"
        risposte = {
            auth.SF_HOST_PRINCIPALE: _RispostaLoginFinta(
                200, "https://private.e-distribuzione.it/PortaleClienti/x", pagina
            ),
            auth.SF_HOST_DIRETTO: _RispostaLoginFinta(
                200, "https://edistribuzione.my.site.com/PortaleClienti/x", pagina
            ),
        }
        client = auth.EdistribuzioneAuthClient(session=None)

        async def _finto_get(*args, **kwargs):
            return risposte[client._host]

        salvate = []
        monkeypatch.setattr(auth, "_get_following_redirects", _finto_get)
        monkeypatch.setattr(
            auth, "_salva_pagina_debug", lambda html, nome: salvate.append(client._host)
        )
        with pytest.raises(auth.EdistribuzioneBloccoAntibot):
            await client.async_begin_login("mario@example.com", "pw")
        assert salvate == [auth.SF_HOST_DIRETTO]


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


class _SessioneACodaOtp:
    """Restituisce le pagine in ordine, una per ogni submit del form OTP."""

    def __init__(self, pagine: list[str]) -> None:
        self._pagine = list(pagine)
        self.chiamate = 0

    def post(self, url, data=None, headers=None):
        self.chiamate += 1
        return _RispostaFinta(self._pagine.pop(0))


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

    async def test_schermata_iniziale_ripresentata_riprova_una_volta(
        self, monkeypatch, tmp_path
    ):
        """Visto il 12/10/2026: al primo submit il portale ha ripresentato la
        schermata "genera OTP" senza spedire il codice; un secondo submit
        poco dopo e' andato a buon fine."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auth, "_ATTESA_RIPROVA_INVIO_OTP_S", 0)
        schermata_iniziale = (
            "<html><body><p>Per completare l'accesso è necessario generare un "
            "codice di verifica (OTP)</p><input type=\"button\" "
            'value="Invia Codice OTP" />' + TestParseOtpPage.HTML_PAGINA_OTP
            + "</body></html>"
        ).replace("OTP_Input", "Campo_Assente")
        session = _SessioneACodaOtp([schermata_iniziale, PAGINA_OTP_CON_CONFERMA])
        client = auth.EdistribuzioneAuthClient(session)
        client._parse_otp_page(TestParseOtpPage.HTML_PAGINA_OTP)
        await client._async_trigger_otp_send()
        assert session.chiamate == 2
        assert client.otp_invio_confermato is True

    async def test_conferma_mancante_su_altra_pagina_non_riprova(
        self, monkeypatch, tmp_path
    ):
        """Senza la schermata iniziale riconoscibile niente secondo submit:
        un invio a vuoto potrebbe far partire un codice in piu'."""
        monkeypatch.chdir(tmp_path)
        session = _SessioneACodaOtp([TestParseOtpPage.HTML_PAGINA_OTP])
        client = auth.EdistribuzioneAuthClient(session)
        client._parse_otp_page(TestParseOtpPage.HTML_PAGINA_OTP)
        await client._async_trigger_otp_send()
        assert session.chiamate == 1

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


class TestPasswordScaduta:
    """Issue #9: dopo l'OTP il portale mostra "Change Password" invece del
    codice di autorizzazione."""

    PAGINA_CAMBIO_PASSWORD = (
        "\r\n<!DOCTYPE HTML>\n<html><head><title>e-distribuzione | Change "
        "Password</title></head><body><form>...</form></body></html>"
    )

    async def test_submit_otp_solleva_eccezione_dedicata(self):
        session = _SessioneACoda([
            _RispostaFinta(
                '<meta name="Location" content="/PortaleClienti/ChangePassword">'
            ),
            _RispostaFinta(self.PAGINA_CAMBIO_PASSWORD),
        ])
        client = auth.EdistribuzioneAuthClient(session)
        client._parse_otp_page(TestParseOtpPage.HTML_PAGINA_OTP)
        with pytest.raises(auth.EdistribuzionePasswordScaduta):
            await client.async_submit_otp("12345")

    def test_riconosciuta_solo_dal_titolo(self):
        assert auth._e_pagina_cambio_password(self.PAGINA_CAMBIO_PASSWORD)
        assert not auth._e_pagina_cambio_password(
            "<html><head><title>Consentire l'accesso?</title></head>"
            '<body><a href="#">Cambia password</a></body></html>'
        )

    def test_non_e_un_errore_di_parsing(self):
        assert issubclass(auth.EdistribuzionePasswordScaduta, auth.EdistribuzioneAuthError)
        assert not issubclass(
            auth.EdistribuzionePasswordScaduta, auth.EdistribuzioneParsingError
        )


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


# ---------------------------------------------------------------------------
# Blocco antibot di Imperva e ripiego sull'host diretto (issue #8)
# ---------------------------------------------------------------------------


# Ridotta dalla risposta reale del 10/10/2026 (403 su
# private.e-distribuzione.it con uno User-Agent da script).
PAGINA_ANTIBOT = (
    '<html style="height:100%"><head><META NAME="ROBOTS" CONTENT="NOINDEX, '
    'NOFOLLOW"></head><body><iframe id="main-iframe" '
    'src="/_Incapsula_Resource?CWUDNSAI=23">Request unsuccessful. Incapsula '
    "incident ID: 1009000050172031670-138368431705620856</iframe></body></html>"
)


class _RispostaToken(_RispostaFinta):
    def __init__(self, body: str, status: int = 200) -> None:
        super().__init__(body)
        self.status = status


class _SessioneTokenPerHost:
    """Risponde al token endpoint in base all'host della richiesta e
    registra gli URL chiamati."""

    def __init__(self, risposte_per_host: dict[str, _RispostaToken]) -> None:
        self._risposte = risposte_per_host
        self.url_chiamati: list[str] = []

    def post(self, url, data=None, headers=None, allow_redirects=None):
        self.url_chiamati.append(url)
        self.ultimi_headers = headers
        for host, risposta in self._risposte.items():
            if url.startswith(f"https://{host}/"):
                return risposta
        raise AssertionError(f"host inatteso: {url}")


TOKEN_OK = '{"access_token": "AT", "refresh_token": "RT2", "instance_url": "I"}'


class TestAntibot:
    def test_pagina_antibot_riconosciuta(self):
        with pytest.raises(auth.EdistribuzioneBloccoAntibot):
            auth._verifica_non_bloccato(PAGINA_ANTIBOT)

    def test_pagina_pardon_our_interruption_riconosciuta(self):
        with pytest.raises(auth.EdistribuzioneBloccoAntibot):
            auth._verifica_non_bloccato("<title>Pardon Our Interruption</title>")

    def test_script_incapsula_su_pagina_normale_non_e_un_blocco(self):
        auth._verifica_non_bloccato(
            '<script src="/_Incapsula_Resource?SWJIYLWA=1"></script><p>Login</p>'
        )

    def test_blocco_antibot_e_sottoclasse_di_auth_error(self):
        assert issubclass(auth.EdistribuzioneBloccoAntibot, auth.EdistribuzioneAuthError)


class TestSuHost:
    def test_host_principale_invariato_finche_non_si_ripiega(self):
        client = auth.EdistribuzioneAuthClient(None)
        url = "https://private.e-distribuzione.it/PortaleClienti/s/login/"
        assert client._su_host(url) == url

    def test_dopo_il_ripiego_riscrive_il_dominio_personalizzato(self):
        client = auth.EdistribuzioneAuthClient(None)
        client._host = auth.SF_HOST_DIRETTO
        assert (
            client._su_host("https://private.e-distribuzione.it/PortaleClienti/x?a=1")
            == "https://edistribuzione.my.site.com/PortaleClienti/x?a=1"
        )

    def test_url_di_altri_host_e_relativi_passano_invariati(self):
        client = auth.EdistribuzioneAuthClient(None)
        client._host = auth.SF_HOST_DIRETTO
        assert client._su_host("eneldist://redirect?code=X") == "eneldist://redirect?code=X"
        assert client._su_host("/PortaleClienti/x") == "/PortaleClienti/x"

    def test_parse_otp_page_usa_l_host_attivo_per_action_relative(self):
        client = auth.EdistribuzioneAuthClient(None)
        client._host = auth.SF_HOST_DIRETTO
        client._parse_otp_page(
            '<form id="f" action="/PortaleClienti/loginflow/loginFlow.apexp">'
            + TestParseOtpPage.HTML_PAGINA_OTP
        )
        assert client._flow.form_action_url.startswith(
            "https://edistribuzione.my.site.com/"
        )

    async def test_consenso_inviato_all_host_attivo(self):
        session = _SessioneACoda([
            _RispostaConLocation("", location="eneldist://redirect?code=C&state=S")
        ])
        client = auth.EdistribuzioneAuthClient(session)
        client._host = auth.SF_HOST_DIRETTO
        await client._async_approva_consenso(HTML_PAGINA_CONSENSO)
        assert session.post_inviati[0]["url"].startswith(
            "https://edistribuzione.my.site.com/"
        )


SF_HOST_TOKEN = "edistribuzione.my.salesforce.com"

APP_BLOCCATA = (
    '{"error":"app_blocked","error_description":'
    '"this app is blocked by Transaction Security"}'
)


class TestRefreshConRipiego:
    """Refresh: prima l'host Salesforce diretto (my.salesforce.com, senza
    Imperva), poi il portale. my.site.com non si usa piu' (app_blocked)."""

    async def test_host_diretto_libero_nessun_ripiego(self):
        session = _SessioneTokenPerHost({
            SF_HOST_TOKEN: _RispostaToken(TOKEN_OK),
        })
        client = auth.EdistribuzioneAuthClient(session)
        tokens = await client.async_refresh_access_token("RT")
        assert tokens.access_token == "AT"
        assert session.url_chiamati == [
            "https://edistribuzione.my.salesforce.com/services/oauth2/token"
        ]

    async def test_host_diretto_app_bloccata_ripiega_sul_portale(self):
        session = _SessioneTokenPerHost({
            SF_HOST_TOKEN: _RispostaToken(APP_BLOCCATA, status=400),
            auth.SF_HOST_PRINCIPALE: _RispostaToken(TOKEN_OK),
        })
        client = auth.EdistribuzioneAuthClient(session)
        tokens = await client.async_refresh_access_token("RT")
        assert tokens.refresh_token == "RT2"
        assert session.url_chiamati[1].startswith(
            "https://private.e-distribuzione.it/PortaleClienti/services/oauth2/token"
        )

    async def test_host_diretto_antibot_ripiega_sul_portale(self):
        session = _SessioneTokenPerHost({
            SF_HOST_TOKEN: _RispostaToken("<title>Pardon Our Interruption</title>"),
            auth.SF_HOST_PRINCIPALE: _RispostaToken(TOKEN_OK),
        })
        client = auth.EdistribuzioneAuthClient(session)
        assert (await client.async_refresh_access_token("RT")).access_token == "AT"

    async def test_entrambi_falliti_propaga_l_errore_dell_host_diretto(self):
        """Il portale fallisce quasi sempre per Imperva: l'errore utile da
        mostrare e' quello dell'host diretto (qui app_blocked)."""
        session = _SessioneTokenPerHost({
            SF_HOST_TOKEN: _RispostaToken(APP_BLOCCATA, status=400),
            auth.SF_HOST_PRINCIPALE: _RispostaToken(PAGINA_ANTIBOT, status=403),
        })
        client = auth.EdistribuzioneAuthClient(session)
        with pytest.raises(auth.EdistribuzioneAppBloccata, match="Transaction Security"):
            await client.async_refresh_access_token("RT")

    async def test_token_revocato_sull_host_diretto_non_ripiega(self):
        """Un refresh_token non valido e' non valido ovunque: niente secondo
        tentativo sul portale, l'errore arriva subito al coordinator."""
        session = _SessioneTokenPerHost({
            SF_HOST_TOKEN: _RispostaToken(
                '{"error":"invalid_grant","error_description":"expired access/refresh token"}',
                status=400,
            ),
        })
        client = auth.EdistribuzioneAuthClient(session)
        with pytest.raises(auth.EdistribuzioneAuthError, match="invalid_grant"):
            await client.async_refresh_access_token("RT")
        assert len(session.url_chiamati) == 1

    async def test_my_site_com_mai_usato_per_il_refresh(self):
        session = _SessioneTokenPerHost({
            SF_HOST_TOKEN: _RispostaToken(TOKEN_OK),
        })
        client = auth.EdistribuzioneAuthClient(session)
        client._host = auth.SF_HOST_DIRETTO
        await client.async_refresh_access_token("RT")
        assert {urlparse(url).hostname for url in session.url_chiamati} == {SF_HOST_TOKEN}

    def test_app_bloccata_e_sottoclasse_di_auth_error(self):
        assert issubclass(auth.EdistribuzioneAppBloccata, auth.EdistribuzioneAuthError)

    async def test_refresh_chiede_esplicitamente_json(self):
        """Con l'Accept di un browser il token endpoint risponde in XML
        (verificato il 12/10/2026 con curl_cffi): va chiesto JSON."""
        session = _SessioneTokenPerHost({
            SF_HOST_TOKEN: _RispostaToken(TOKEN_OK),
        })
        client = auth.EdistribuzioneAuthClient(session)
        await client.async_refresh_access_token("RT")
        assert session.ultimi_headers == {"Accept": "application/json"}

    async def test_refresh_token_vecchio_mantenuto_se_non_ne_arriva_uno_nuovo(self):
        session = _SessioneTokenPerHost({
            SF_HOST_TOKEN: _RispostaToken('{"access_token": "AT"}'),
        })
        client = auth.EdistribuzioneAuthClient(session)
        assert (await client.async_refresh_access_token("RT")).refresh_token == "RT"
