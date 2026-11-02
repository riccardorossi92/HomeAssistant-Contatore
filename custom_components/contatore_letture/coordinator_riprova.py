"""Coordinator a ciclo lungo che dopo un errore riprova a breve.

Areti e Ireti interrogano il distributore una volta al giorno
(update_interval = 24 ore). Un DataUpdateCoordinator riprogramma il giro
successivo dopo lo stesso update_interval anche se l'aggiornamento e'
fallito: un errore passeggero al primo giro (tipicamente la rete ancora
giu' al riavvio di Home Assistant) lascia quindi le entita' non disponibili
per un giorno intero (issue #17). Qui, dopo un errore, l'intervallo scende
a INTERVALLO_RIPROVA e torna quello normale appena un aggiornamento riesce.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

INTERVALLO_RIPROVA = timedelta(minutes=30)


class CoordinatorRiprovaDopoErrore(DataUpdateCoordinator[dict]):
    """Le sottoclassi implementano _async_aggiorna al posto di
    _async_update_data."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._intervallo_normale = self.update_interval

    async def _async_aggiorna(self) -> dict:
        raise NotImplementedError

    async def _async_update_data(self) -> dict:
        try:
            dati = await self._async_aggiorna()
        except ConfigEntryAuthFailed:
            # Credenziali da reinserire: Home Assistant non riprogramma il
            # giro e un nuovo tentativo a breve sarebbe inutile.
            raise
        except Exception:
            self.update_interval = INTERVALLO_RIPROVA
            raise
        self.update_interval = self._intervallo_normale
        return dati
