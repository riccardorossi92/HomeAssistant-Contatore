"""Test degli helper per collegare i dispositivi al loro "padre".

Il punto delicato e' la scelta a runtime tra via_device_id (HA 2026.8+) e
via_device (versioni precedenti): un errore qui, su HA 2026.9, puo'
impedire del tutto la creazione delle entita'.
"""
from __future__ import annotations

import pytest

from custom_components.contatore_letture import device_helpers as dh

DOMAIN = "contatore_letture"


@pytest.mark.parametrize(
    ("versione", "atteso"),
    [
        ("2026.8.0", True),
        ("2026.9.1", True),
        ("2027.1.0", True),
        ("2026.7.9", False),
        ("2025.4.0", False),
        ("non-una-versione", False),
    ],
)
def test_supporta_via_device_id_per_versione(monkeypatch, versione, atteso):
    monkeypatch.setattr(dh, "HA_VERSION", versione)
    assert dh.supporta_via_device_id() is atteso


def test_collega_al_padre_usa_via_device_id_se_c_e_l_id():
    info = {"identifiers": {(DOMAIN, "e1_POD1")}}
    risultato = dh.collega_al_padre(info, {(DOMAIN, "e1")}, "device-interno-42")
    assert risultato is info
    assert info["via_device_id"] == "device-interno-42"
    assert "via_device" not in info


def test_collega_al_padre_ripiega_su_via_device_senza_id():
    info = {"identifiers": {(DOMAIN, "e1_POD1")}}
    dh.collega_al_padre(info, {(DOMAIN, "e1")}, None)
    assert info["via_device"] == (DOMAIN, "e1")
    assert "via_device_id" not in info
