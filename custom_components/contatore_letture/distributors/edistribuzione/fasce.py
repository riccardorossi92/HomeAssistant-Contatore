"""Consumo/immissione del mese precedente per fascia (F1, F2, F3).

Fonte: `querymonthlytimeofuse` di E-Distribuzione (api.async_get_monthly_time_of_use),
una riga per mese. Risposta reale, ottobre 2026 (POD con impianto, contratto a
tre fasce), con `energyType` "A1" (prelevato) o "A2" (immesso):

    [{"readings": {"energyType": "A1",
                   "energyT1": "10.587", "energyT2": "27.837",
                   "energyT3": "77.366", "energyT4": "115.790",
                   "peakPowerT1": "1.94", ...},
      "yearMonth": "202610"}]

Verificato contro le letture ufficiali (`reading`): la differenza tra le
letture cumulative del 1 novembre e del 1 ottobre dà gli stessi valori per
fascia (T1, T2, T3). `energyT4` è il totale del mese e coincide con il
sensore "Consumo mese precedente"; per questo non lo si legge qui.

T1/T2/T3 sono lette come F1/F2/F3, l'abbinamento standard. Le fasce non attive
sul contratto non hanno un valore e restano fuori dal risultato.
"""
from __future__ import annotations

from datetime import date, timedelta

FASCE: tuple[str, ...] = ("F1", "F2", "F3")


def mese_precedente(oggi: date) -> tuple[date, date, str]:
    """Primo e ultimo giorno del mese di calendario prima di `oggi`, e il suo
    "AAAAMM" come lo scrive l'API."""
    fine = oggi.replace(day=1) - timedelta(days=1)
    return fine.replace(day=1), fine, fine.strftime("%Y%m")


def fasce_da_risposta(righe: list[dict], anno_mese: str) -> dict[str, float] | None:
    """kWh per fascia ({"F1": ..., "F2": ..., "F3": ...}) del mese `anno_mese`.

    None se la risposta non contiene quel mese o nessuna fascia leggibile
    (mese non ancora pubblicato): non è uno zero.
    """
    for riga in righe:
        if riga.get("yearMonth") != anno_mese:
            continue
        letture = riga.get("readings") or {}
        fasce: dict[str, float] = {}
        for indice, fascia in enumerate(FASCE, start=1):
            try:
                fasce[fascia] = float(letture[f"energyT{indice}"])
            except (KeyError, TypeError, ValueError):
                continue
        return fasce or None
    return None
