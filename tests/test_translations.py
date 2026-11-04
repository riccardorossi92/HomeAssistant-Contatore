"""Coerenza tra strings.json e le traduzioni.

Le integrazioni custom leggono i testi del config flow solo da
translations/<lingua>.json, con en.json come fallback: strings.json non viene
letto a runtime. strings.json e it.json restano identici; en.json e' la
traduzione inglese e deve avere le stesse chiavi e gli stessi segnaposto,
altrimenti Home Assistant mostra testi mancanti o solleva KeyError nel format.
"""

import json
import re
from pathlib import Path

COMPONENTE = Path(__file__).parent.parent / "custom_components" / "contatore_letture"
SEGNAPOSTO = re.compile(r"\{[^{}]*\}")


def _carica(*parti: str) -> dict:
    return json.loads((COMPONENTE.joinpath(*parti)).read_text(encoding="utf-8"))


def _foglie(nodo, percorso=()):
    if isinstance(nodo, dict):
        for chiave, valore in nodo.items():
            yield from _foglie(valore, (*percorso, chiave))
    else:
        yield ".".join(percorso), nodo


def test_strings_e_it_json_sono_identici():
    strings = (COMPONENTE / "strings.json").read_text(encoding="utf-8")
    assert (COMPONENTE / "translations" / "it.json").read_text(encoding="utf-8") == strings


def test_en_json_ha_le_stesse_chiavi_di_it_json():
    it = dict(_foglie(_carica("translations", "it.json")))
    en = dict(_foglie(_carica("translations", "en.json")))
    assert set(en) == set(it)


def test_en_json_ha_gli_stessi_segnaposto_di_it_json():
    it = dict(_foglie(_carica("translations", "it.json")))
    en = dict(_foglie(_carica("translations", "en.json")))
    diversi = {
        chiave: (sorted(SEGNAPOSTO.findall(it[chiave])), sorted(SEGNAPOSTO.findall(en[chiave])))
        for chiave in it
        if sorted(SEGNAPOSTO.findall(it[chiave])) != sorted(SEGNAPOSTO.findall(en[chiave]))
    }
    assert not diversi


def test_en_json_e_davvero_tradotto():
    it = dict(_foglie(_carica("translations", "it.json")))
    en = dict(_foglie(_carica("translations", "en.json")))
    uguali = [c for c in it if en[c] == it[c] and len(it[c]) > 20]
    assert not uguali
