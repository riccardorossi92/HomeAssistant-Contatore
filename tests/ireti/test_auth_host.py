"""Il login a form di Ireti manda le credenziali solo a un'action https su un
host di HOST_LOGIN_AMMESSI, e quando si ferma dice quale host ha rifiutato e
a che passo."""
from __future__ import annotations

import logging

import pytest

from custom_components.contatore_letture.distributors.ireti import auth
from custom_components.contatore_letture.distributors.ireti.const import HOST_LOGIN_AMMESSI


def test_elenco_degli_host_in_una_sola_costante():
    assert HOST_LOGIN_AMMESSI == ("smartpod.ireti.it",)


@pytest.mark.parametrize(
    "url",
    [
        "https://smartpod.ireti.it/auth/realms/IRENSmartPOD/login-actions/authenticate?session_code=x",
        "https://SmartPOD.Ireti.it/auth/realms/IRENSmartPOD/login-actions/authenticate",
    ],
)
def test_host_ammessi(url):
    auth._verifica_host(url, "prova")


@pytest.mark.parametrize(
    ("url", "motivo"),
    [
        ("https://altro.example/login-actions/authenticate?session_code=SESSIONE", "ammessi"),
        ("http://smartpod.ireti.it/auth/realms/IRENSmartPOD/login-actions/authenticate", "https"),
        ("https://smartpod.ireti.it.altro.example/login-actions/authenticate", "ammessi"),
        ("https://smartpod.ireti.it@altro.example/login-actions/authenticate", "ammessi"),
        ("/auth/realms/IRENSmartPOD/login-actions/authenticate", "relativo"),
    ],
)
def test_host_rifiutato_nell_errore_e_nel_log(url, motivo, caplog):
    caplog.set_level(logging.ERROR, logger=auth.__name__)
    with pytest.raises(auth.IretiHostNonAmmesso) as errore:
        auth._verifica_host(url, "form di login Keycloak")

    messaggio = str(errore.value)
    assert '"form di login Keycloak"' in messaggio
    assert motivo in messaggio
    assert "HOST_LOGIN_AMMESSI" in messaggio
    assert "SESSIONE" not in messaggio
    assert messaggio in caplog.text
    # Il config flow gestisce gia' IretiAuthError.
    assert isinstance(errore.value, auth.IretiAuthError)
    assert not isinstance(errore.value, auth.IretiInvalidCredentials)
