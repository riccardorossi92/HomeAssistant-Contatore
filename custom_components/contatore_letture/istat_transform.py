"""Trasformazione pura della lista comuni in albero regione/provincia/comune.

Nessuna dipendenza da Home Assistant o da librerie di rete: usata sia da
istat_comuni.py (fetch live a runtime, dentro l'integrazione) sia da
scripts/update_istat_snapshot.py (rigenerazione dello snapshot bundlato,
nella GitHub Action) - stessa funzione, cosi' le due sorgenti non possono
disallinearsi nel formato prodotto.

Fonte del dato atteso: https://github.com/matteocontrini/comuni-json
(mirror JSON del dataset ISTAT "Codici statistici delle unita'
amministrative territoriali").
"""
from __future__ import annotations

ComuniTree = dict[str, dict[str, dict[str, dict[str, str]]]]


def comuni_list_to_tree(data: list[dict]) -> ComuniTree:
    """Converte la lista piatta di comuni in {regione: {provincia: {comune: codici}}}.

    Solleva ValueError se 'data' non ha la forma attesa (lista non vuota di
    dict con le chiavi 'nome', 'regione', 'provincia', 'codice').
    """
    if not isinstance(data, list) or not data:
        raise ValueError("Formato dati comuni inatteso (lista vuota o non valida)")

    tree: ComuniTree = {}
    for c in data:
        regione = c["regione"]["nome"]
        provincia = c["provincia"]["nome"]
        comune = c["nome"]
        tree.setdefault(regione, {}).setdefault(provincia, {})[comune] = {
            "codice_regione": c["regione"]["codice"],
            "codice_provincia": c["provincia"]["codice"],
            "codice_comune": c["codice"],
        }
    return tree
