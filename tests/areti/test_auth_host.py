"""Il login Areti segue redirect e pagine-ponte solo verso gli host di
HOST_LOGIN_AMMESSI, e quando si ferma dice quale host ha rifiutato e a che
passo."""
from __future__ import annotations

import logging

import pytest

from custom_components.contatore_letture.distributors.areti import auth
from custom_components.contatore_letture.distributors.areti.const import HOST_LOGIN_AMMESSI


def test_elenco_degli_host_in_una_sola_costante():
    assert "areariservataclienti.areti.it" in HOST_LOGIN_AMMESSI
    assert all(voce == voce.lower() for voce in HOST_LOGIN_AMMESSI)


@pytest.mark.parametrize(
    "url",
    [
        "https://areariservataclienti.areti.it/portaleareti/secur/frontdoor.jsp?sid=x",
        "https://areariservataclienti.areti.it/portaleareti/loginflow/loginFlowOnly.apexp",
        "https://aretispa.my.salesforce.com/secur/frontdoor.jsp",
        "https://aretispa--c.vf.force.com/apex/x",
    ],
)
def test_host_ammessi(url):
    auth._verifica_host(url, "prova")


@pytest.mark.parametrize(
    ("url", "motivo"),
    [
        ("https://altro.example/x?sid=SESSIONE", "ammessi"),
        ("http://areariservataclienti.areti.it/portaleareti/", "https"),
        ("https://areariservataclienti.areti.it.altro.example/", "ammessi"),
        ("/portaleareti/s/", "relativo"),
    ],
)
def test_host_rifiutato_nell_errore_e_nel_log(url, motivo, caplog):
    caplog.set_level(logging.ERROR, logger=auth.__name__)
    with pytest.raises(auth.AretiHostNonAmmesso) as errore:
        auth._verifica_host(url, "redirect dopo le credenziali")

    messaggio = str(errore.value)
    assert '"redirect dopo le credenziali"' in messaggio
    assert motivo in messaggio
    assert "HOST_LOGIN_AMMESSI" in messaggio
    assert "SESSIONE" not in messaggio
    assert messaggio in caplog.text
    assert isinstance(errore.value, auth.AretiAuthError)
