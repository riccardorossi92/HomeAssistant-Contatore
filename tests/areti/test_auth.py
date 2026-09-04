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
