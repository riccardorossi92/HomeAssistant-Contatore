"""Rigenera data/istat_comuni_snapshot.json dalla stessa sorgente usata a
runtime dall'integrazione (istat_comuni.py), tramite la stessa funzione di
trasformazione pura (istat_transform.comuni_csv_to_tree) - cosi' lo
snapshot bundlato non puo' disallinearsi nel formato dal fetch live.

Uso:
    python3 scripts/update_istat_snapshot.py

Exit code:
    0 = nessuna modifica rispetto allo snapshot committato
    1 = snapshot aggiornato (usato dalla Action per decidere se aprire una PR)
    2 = errore (fetch fallito, formato dati inatteso, ...)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

# istat_transform.py non ha dipendenze esterne: importabile direttamente
# senza installare homeassistant in CI.
sys.path.insert(
    0, str(Path(__file__).parent.parent / "custom_components" / "contatore_letture")
)
from istat_transform import comuni_csv_to_tree  # noqa: E402

# Permalink ufficiale ISTAT, dichiarato immutabile ad ogni aggiornamento.
# Il file e' in cp1252, non UTF-8.
ISTAT_CSV_URL = (
    "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
)
ISTAT_CSV_ENCODING = "cp1252"
SNAPSHOT_PATH = (
    Path(__file__).parent.parent
    / "custom_components"
    / "contatore_letture"
    / "data"
    / "istat_comuni_snapshot.json"
)


def main() -> int:
    try:
        resp = requests.get(ISTAT_CSV_URL, timeout=60)
        resp.raise_for_status()
        nuovo_albero = comuni_csv_to_tree(resp.content.decode(ISTAT_CSV_ENCODING))
    except Exception as exc:  # noqa: BLE001
        print(f"Errore nel fetch/trasformazione: {exc}", file=sys.stderr)
        return 2

    nuovo_json = json.dumps(nuovo_albero, ensure_ascii=False, indent=2, sort_keys=True)

    precedente_json = (
        SNAPSHOT_PATH.read_text(encoding="utf-8") if SNAPSHOT_PATH.exists() else None
    )

    if nuovo_json == precedente_json:
        print("Snapshot invariato.")
        return 0

    n_regioni = len(nuovo_albero)
    n_comuni = sum(
        len(comuni)
        for province in nuovo_albero.values()
        for comuni in province.values()
    )
    print(
        f"Snapshot cambiato: {n_regioni} regioni, {n_comuni} comuni "
        f"(era: {SNAPSHOT_PATH.exists() and 'presente' or 'assente'})."
    )
    SNAPSHOT_PATH.write_text(nuovo_json, encoding="utf-8")
    return 1


if __name__ == "__main__":
    sys.exit(main())
