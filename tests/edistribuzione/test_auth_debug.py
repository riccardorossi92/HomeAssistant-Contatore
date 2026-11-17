"""Pagine di debug ripulite prima di arrivare sul disco o nei log, e
cancellate dopo un accesso riuscito."""
from __future__ import annotations

import logging

from custom_components.contatore_letture.distributors.edistribuzione import auth

EMAIL = "mario.rossi@example.com"
PASSWORD = "Pa$$w0rd"

PAGINA = (
    "<title>Verifica identita'</title>"
    f"<p>Abbiamo inviato un codice a {EMAIL}</p>"
    '<input type="hidden" name="com.salesforce.visualforce.ViewState" value="VIEWSTATE123">'
    '<input type="hidden" name="com.salesforce.visualforce.ViewStateCSRF" value="CSRF999">'
    '<input type="text" name="thePage:j_id2:i:f:pb:d:element___input____OTP_Input" value="">'
    "<script>window.location.replace('/secur/frontdoor.jsp?sid=00Dxx!SESSIONE&retURL=%2Fhome');"
    "</script>"
    '<a href="eneldist://redirect?code=CODICE123&amp;state=abc">continua</a>'
    '{"access_token":"AT-SEGRETO","refresh_token":"RT-SEGRETO","instance_url":"https://x"}'
    "username=mario.rossi%40example.com&password=Pa%24%24w0rd"
)


def test_oscura_credenziali_token_e_viewstate_ma_lascia_la_struttura():
    pulita = auth._oscura_per_debug(PAGINA, (EMAIL, PASSWORD))

    for segreto in (
        "mario.rossi",
        "VIEWSTATE123",
        "CSRF999",
        "SESSIONE",
        "CODICE123",
        "AT-SEGRETO",
        "RT-SEGRETO",
        "Pa%24%24w0rd",
        PASSWORD,
    ):
        assert segreto not in pulita
    for utile in (
        "<title>Verifica identita'</title>",
        "Abbiamo inviato un codice a",
        "ViewStateCSRF",
        "OTP_Input",
        "frontdoor.jsp?sid=***",
        "retURL=%2Fhome",
        "state=abc",
        '"instance_url":"https://x"',
    ):
        assert utile in pulita


def test_oscura_le_email_anche_senza_conoscerle():
    assert auth._oscura_per_debug("scrivi a qualcuno@dominio.it") == "scrivi a ***@***"


def test_pagina_salvata_su_disco_gia_oscurata(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(auth, "_DIR_DEBUG", tmp_path / "debug")
    caplog.set_level(logging.DEBUG, logger=auth.__name__)

    auth._salva_pagina_debug(PAGINA, "otp_send_debug.html", (EMAIL, PASSWORD))

    salvata = (tmp_path / "debug" / "otp_send_debug.html").read_text(encoding="utf-8")
    assert salvata == auth._oscura_per_debug(PAGINA, (EMAIL, PASSWORD))
    assert "VIEWSTATE123" not in salvata


def test_anteprima_nel_log_oscurata(caplog):
    caplog.set_level(logging.INFO, logger=auth.__name__)
    auth._log_parsing_failure_context(PAGINA, "campo", segreti=(EMAIL, PASSWORD))
    assert "Campo 'campo' non trovato" in caplog.text
    assert EMAIL not in caplog.text
    assert "VIEWSTATE123" not in caplog.text


def _prepara_cartella(tmp_path, monkeypatch):
    cartella = tmp_path / "contatore_letture" / "debug"
    cartella.mkdir(parents=True)
    (cartella / "otp_send_debug.html").write_text("vecchia pagina", encoding="utf-8")
    monkeypatch.setattr(auth, "_DIR_DEBUG", cartella)
    return cartella


def test_pagine_cancellate_dopo_un_accesso_riuscito(tmp_path, monkeypatch, caplog):
    cartella = _prepara_cartella(tmp_path, monkeypatch)
    caplog.set_level(logging.INFO, logger=auth.__name__)

    auth._pulisci_pagine_debug()

    assert not cartella.exists()
    assert not cartella.parent.exists()


def test_pagine_tenute_se_il_debug_e_ancora_attivo(tmp_path, monkeypatch, caplog):
    cartella = _prepara_cartella(tmp_path, monkeypatch)
    caplog.set_level(logging.DEBUG, logger=auth.__name__)

    auth._pulisci_pagine_debug()

    assert (cartella / "otp_send_debug.html").exists()
