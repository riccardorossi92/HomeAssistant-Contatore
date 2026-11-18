"""Il login E-Distribuzione segue redirect, pagine-ponte e form solo verso gli
host di HOST_LOGIN_AMMESSI, e quando si ferma dice quale host ha rifiutato e
a che passo."""
from __future__ import annotations

import logging

import pytest
from yarl import URL

from custom_components.contatore_letture.distributors.edistribuzione import auth
from custom_components.contatore_letture.distributors.edistribuzione.const import (
    HOST_LOGIN_AMMESSI,
)


def test_elenco_degli_host_in_una_sola_costante():
    # Confronto con ==, non "in": e' la lista degli host, non una sottostringa
    # di un URL (CodeQL py/incomplete-url-substring-sanitization).
    assert any(voce == "private.e-distribuzione.it" for voce in HOST_LOGIN_AMMESSI)
    assert any(voce == "edistribuzione.my.salesforce.com" for voce in HOST_LOGIN_AMMESSI)
    assert all(voce == voce.lower() for voce in HOST_LOGIN_AMMESSI)


@pytest.mark.parametrize(
    "url",
    [
        "https://private.e-distribuzione.it/PortaleClienti/s/login/",
        "https://edistribuzione.my.site.com/PortaleClienti/secur/frontdoor.jsp",
        "https://edistribuzione.my.salesforce.com/services/oauth2/token",
        "https://edistribuzione--c.vf.force.com/apex/loginFlow",
        "https://PRIVATE.E-Distribuzione.it/PortaleClienti/",
        "/PortaleClienti/loginflow/loginFlow.apexp",
        "eneldist://redirect?code=abc",
    ],
)
def test_host_ammessi(url):
    auth._verifica_host(url, "prova")


@pytest.mark.parametrize(
    ("url", "host", "motivo"),
    [
        ("https://altro.example/raccolta?sid=00Dxx!SESSIONE", "altro.example", "ammessi"),
        ("http://private.e-distribuzione.it/PortaleClienti/", "private.e-distribuzione.it", "https"),
        (
            "https://private.e-distribuzione.it.altro.example/",
            "private.e-distribuzione.it.altro.example",
            "ammessi",
        ),
        ("https://nonforce.com/", "nonforce.com", "ammessi"),
    ],
)
def test_host_rifiutato_nell_errore_e_nel_log(url, host, motivo, caplog):
    caplog.set_level(logging.ERROR, logger=auth.__name__)
    with pytest.raises(auth.EdistribuzioneHostNonAmmesso) as errore:
        auth._verifica_host(url, "frontdoor dopo le credenziali")

    messaggio = str(errore.value)
    assert host in messaggio
    assert '"frontdoor dopo le credenziali"' in messaggio
    assert motivo in messaggio
    assert "HOST_LOGIN_AMMESSI" in messaggio
    assert "SESSIONE" not in messaggio  # niente query: puo' contenere token
    assert messaggio in caplog.text
    # Rientra tra gli errori che il config flow gestisce gia'.
    assert isinstance(errore.value, auth.EdistribuzioneParsingError)


class _Risposta:
    def __init__(self, url: str, location: str | None = None) -> None:
        self.url = URL(url)
        self.headers = {"Location": location} if location else {}

    def close(self) -> None:
        pass


class _Sessione:
    def __init__(self, risposte: list[_Risposta]) -> None:
        self._risposte = list(risposte)
        self.chiamate: list[str] = []

    async def get(self, url, **_kwargs):
        self.chiamate.append(str(url))
        return self._risposte.pop(0)


async def test_redirect_verso_altro_host_fermato_prima_della_richiesta():
    frontdoor = "https://private.e-distribuzione.it/PortaleClienti/secur/frontdoor.jsp"
    sessione = _Sessione(
        [
            _Risposta(frontdoor, location="/PortaleClienti/apex/ponte"),
            _Risposta(
                "https://private.e-distribuzione.it/PortaleClienti/apex/ponte",
                location="https://altro.example/raccogli?sid=SEGRETO",
            ),
        ]
    )

    with pytest.raises(auth.EdistribuzioneHostNonAmmesso) as errore:
        await auth._get_following_redirects(
            sessione, frontdoor, passo="frontdoor dopo le credenziali"
        )

    assert '"frontdoor dopo le credenziali, redirect 2"' in str(errore.value)
    assert "altro.example" in str(errore.value)
    assert "SEGRETO" not in str(errore.value)
    assert len(sessione.chiamate) == 2  # l'host estraneo non riceve nessuna richiesta


async def test_redirect_tra_host_ammessi_arriva_a_destinazione():
    inizio = "https://private.e-distribuzione.it/PortaleClienti/services/oauth2/authorize"
    finale = _Risposta("https://edistribuzione--c.vf.force.com/apex/loginFlow")
    sessione = _Sessione(
        [_Risposta(inizio, location="https://edistribuzione--c.vf.force.com/apex/loginFlow"), finale]
    )

    risposta = await auth._get_following_redirects(sessione, inizio, passo="authorize")

    assert risposta is finale


def test_form_otp_con_action_su_altro_host_rifiutato():
    client = auth.EdistribuzioneAuthClient(session=None)
    pagina = (
        '<form action="https://altro.example/raccogli">'
        '<input name="com.salesforce.visualforce.ViewState" value="a">'
        '<input name="com.salesforce.visualforce.ViewStateVersion" value="b">'
        '<input name="com.salesforce.visualforce.ViewStateMAC" value="c">'
        '<input name="com.salesforce.visualforce.ViewStateCSRF" value="d">'
        "</form>"
    )
    with pytest.raises(auth.EdistribuzioneHostNonAmmesso) as errore:
        client._parse_otp_page(pagina)
    assert '"form del codice OTP"' in str(errore.value)
