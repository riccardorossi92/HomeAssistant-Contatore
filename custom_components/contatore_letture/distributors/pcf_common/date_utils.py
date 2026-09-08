"""Helper di date puri (nessun import da homeassistant).

Vivono qui e non in coordinator.py perche' servono anche ad api.py, che
di proposito non importa homeassistant (vedi requirements_test.txt): il
config/options flow puo' cosi' validare un POD senza tirarsi dietro
l'intero stack HA.

Dal 08/09/2026 i manuali Unareti/Duereti dichiarano che le CURVE sono
disponibili "solo fino al mese appena concluso": la granularita' utile e'
il mese solare, e queste funzioni la incapsulano (formato "YYYY-MM",
primo/ultimo giorno, mese successivo, ultimo mese chiuso).
"""
from __future__ import annotations

from datetime import date, timedelta

# Formato del cursore mensile persistito (CONF_MESE_DA_IMPORTARE) e di
# qualunque mese scambiato tra coordinator e helper: "2026-08".
FORMATO_MESE = "%Y-%m"


def mese_str(giorno: date) -> str:
    """date -> "YYYY-MM" del mese che contiene quel giorno."""
    return giorno.strftime(FORMATO_MESE)


def primo_giorno_mese(mese: str) -> date:
    """"YYYY-MM" -> primo giorno di quel mese."""
    anno, mm = int(mese[:4]), int(mese[5:7])
    return date(anno, mm, 1)


def ultimo_giorno_mese(mese: str) -> date:
    """"YYYY-MM" -> ultimo giorno di quel mese."""
    return primo_giorno_mese(mese_successivo(mese)) - timedelta(days=1)


def mese_successivo(mese: str) -> str:
    """"YYYY-MM" -> "YYYY-MM" del mese successivo."""
    anno, mm = int(mese[:4]), int(mese[5:7])
    return f"{anno + 1}-01" if mm == 12 else f"{anno}-{mm + 1:02d}"


def mese_precedente(mese: str) -> str:
    """"YYYY-MM" -> "YYYY-MM" del mese precedente."""
    anno, mm = int(mese[:4]), int(mese[5:7])
    return f"{anno - 1}-12" if mm == 1 else f"{anno}-{mm - 1:02d}"


def ultimo_mese_chiuso(oggi: date) -> str:
    """"YYYY-MM" del mese solare precedente a 'oggi': l'ultimo per cui i
    manuali PCF garantiscono la disponibilita' delle CURVE ("fino al mese
    appena concluso", "non relative al mese corrente")."""
    return mese_precedente(mese_str(oggi))


def mese_e_chiuso(mese: str, oggi: date) -> bool:
    """True se 'mese' e' antecedente al mese corrente, quindi richiedibile."""
    return primo_giorno_mese(mese) < oggi.replace(day=1)


def mese_precedente_completo(oggi: date) -> tuple[date, date]:
    """Primo/ultimo giorno del mese precedente a 'oggi'.

    Usato come etichetta di default per un ticket forzato a mano
    (async_forza_ticket) e come giorno di prova per la verifica del POD in
    fase di configurazione (async_valida_pod): il mese appena concluso e'
    sempre disponibile, un singolo giorno del mese corrente no.
    """
    mese = ultimo_mese_chiuso(oggi)
    return primo_giorno_mese(mese), ultimo_giorno_mese(mese)
