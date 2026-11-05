"""Edyna nel registry dei distributori: riconoscimento via P.IVA ARERA."""
from custom_components.contatore_letture.distributors import DISTRIBUTOR_REGISTRY, PIVA_TO_KEY


def test_la_piva_arera_porta_a_edyna():
    # P.IVA restituita da ARERA per Bolzano e Salorno (05/11/2026).
    assert PIVA_TO_KEY["02689370217"] == "edyna"


def test_voce_di_registro():
    info = DISTRIBUTOR_REGISTRY["edyna"]
    assert info["kind"] == "edyna" and info["display_name"] == "Edyna"
    assert any("nome utente" in r.lower() for r in info["required_info"])
