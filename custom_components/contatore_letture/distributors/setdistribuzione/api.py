"""Client per le API REST di mySET (agw.setdistribuzione.it).

Tutte con 'Authorization: Bearer <access_token>' (auth.py), più l'Origin
del portale come fa il browser. Forma delle risposte documentata in
documentation/protocols/set-distribuzione-protocol.md; tutte confermate
sulla cattura reale del 13/10/2026 (account con un POD attivo):

  - GET /secure/profile/customer: profili dell'account (fiscalCode +
    profile, es. "Retail_SET"), servono a costruire i path successivi;
  - GET /secure/utility/{fiscalCode}/active?profile=...: forniture, con
    podPdr, contractId, utilityType ("ENERGY");
  - GET /secure/utility/{fiscalCode}/{contractId}/consumption: consumi.
    Senza 'month' totali mensili dell'anno, con 'month' totali giornalieri,
    con 'month' e 'day' le 24 ore del giorno con i quarti d'ora. 'month' è
    0-based (month=9 restituisce ottobre), 'day' no.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import API_BASE, HEADERS_BROWSER, HEADERS_ORIGINE_PORTALE

_LOGGER = logging.getLogger(__name__)

# Unico tipo di fornitura importabile: il portale gestisce anche gas e
# teleriscaldamento del gruppo, che non sono di SET Distribuzione.
UTILITY_TYPE_ENERGIA = "ENERGY"


class SetDistribuzioneApiError(Exception):
    """Chiamata fallita (trasporto, HTTP inatteso, o risposta inattesa)."""


class SetDistribuzioneApiClient:
    def __init__(self, session: aiohttp.ClientSession, access_token: str) -> None:
        self._session = session
        self._headers = {
            **HEADERS_BROWSER,
            **HEADERS_ORIGINE_PORTALE,
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

    async def _get(
        self, path: str, params: dict[str, Any] | None = None, *, vuoto_se_404: bool = False
    ) -> Any:
        try:
            async with self._session.get(
                f"{API_BASE}{path}", params=params, headers=self._headers
            ) as resp:
                if resp.status == 404 and vuoto_se_404:
                    return None
                if resp.status != 200:
                    testo = await resp.text()
                    raise SetDistribuzioneApiError(
                        f"GET {path}: HTTP {resp.status}: {testo[:200]!r}"
                    )
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise SetDistribuzioneApiError(f"Errore di trasporto chiamando GET {path}: {err}") from err
        except ValueError as err:
            raise SetDistribuzioneApiError(f"GET {path}: risposta non JSON: {err}") from err

    async def async_get_profili(self) -> list[dict[str, Any]]:
        """GET /secure/profile/customer - un elemento per profilo
        dell'account, con 'fiscalCode' e 'profile'. Lista vuota per un
        account senza forniture (profilo "Prospect", cattura del 13/09/2026)."""
        corpo = await self._get("/secure/profile/customer")
        dati = corpo.get("data") if isinstance(corpo, dict) else None
        if not isinstance(dati, list):
            raise SetDistribuzioneApiError(f"Risposta inattesa da /secure/profile/customer: {corpo!r}")
        return [p for p in dati if isinstance(p, dict)]

    async def async_get_forniture(self, fiscal_code: str, profilo: str) -> list[dict[str, Any]]:
        """GET /secure/utility/{fiscalCode}/active - forniture del profilo."""
        corpo = await self._get(
            f"/secure/utility/{quote(fiscal_code, safe='')}/active", {"profile": profilo}
        )
        dati = corpo.get("data") if isinstance(corpo, dict) else None
        if not isinstance(dati, dict):
            raise SetDistribuzioneApiError(f"Risposta inattesa da utility/active: {corpo!r}")
        return [u for u in dati.get("utilities") or [] if isinstance(u, dict)]

    async def async_get_pod_account(self) -> list[dict[str, str]]:
        """Tutti i POD elettrici dell'account, con quello che serve a
        chiederne i consumi: [{"pod", "fiscal_code", "profile",
        "contract_id", "nome"}]. Un POD presente in più profili compare
        una volta sola (il primo profilo che lo elenca)."""
        risultato: dict[str, dict[str, str]] = {}
        for profilo in await self.async_get_profili():
            fiscal_code, nome_profilo = profilo.get("fiscalCode"), profilo.get("profile")
            if not fiscal_code or not nome_profilo:
                continue
            for fornitura in await self.async_get_forniture(fiscal_code, nome_profilo):
                pod = fornitura.get("podPdr")
                if (
                    fornitura.get("utilityType") != UTILITY_TYPE_ENERGIA
                    or not pod
                    or not fornitura.get("contractId")
                    or pod in risultato
                ):
                    continue
                risultato[pod] = {
                    "pod": pod,
                    "fiscal_code": fiscal_code,
                    "profile": nome_profilo,
                    "contract_id": str(fornitura["contractId"]),
                    "nome": fornitura.get("name") or "",
                }
        return list(risultato.values())

    async def async_get_consumi_giorno(
        self, fiscal_code: str, profilo: str, contract_id: str, pod: str, giorno: date
    ) -> list[dict[str, Any]]:
        """Le ore di un giorno, ciascuna con 'hour', 'total' e 'quarters'
        (4 valori in kWh). Lista vuota se il giorno non è ancora
        disponibile: nella cattura un giorno non pubblicato semplicemente
        manca dall'elenco mensile, la risposta del dettaglio per un giorno
        così non è mai stata vista (vuota o 404, entrambe gestite)."""
        corpo = await self._get(
            f"/secure/utility/{quote(fiscal_code, safe='')}/{quote(contract_id, safe='')}"
            "/consumption",
            {
                "consumptionRange": "F1_F2_F3",
                "consumptionType": "A1",
                "day": giorno.day,
                "month": giorno.month - 1,
                "profile": profilo,
                "supplyPoint": pod,
                "year": giorno.year,
            },
            vuoto_se_404=True,
        )
        if corpo is None:
            return []
        dati = corpo.get("data") if isinstance(corpo, dict) else None
        if dati is None:
            return []
        if not isinstance(dati, dict):
            raise SetDistribuzioneApiError(f"Risposta inattesa da consumption: {corpo!r}")
        return [c for c in dati.get("consumptions") or [] if isinstance(c, dict)]
