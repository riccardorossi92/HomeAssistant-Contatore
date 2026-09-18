"""Client per le API REST del portale Ireti (smartpod.ireti.it).

Tutte via Bearer token (auth.py) + gli header "da browser" di
HEADERS_BROWSER (const.py) - senza quelli, /users/* e /readings/* vanno in
timeout (WAF). Endpoint e forma delle risposte documentati per esteso in
documentation/protocols/ireti-protocol.md.

Stato di conferma per endpoint (importante per chi tocca questo file):
  - company-by-host, consumer, pods: confermati su dati reali (31/08 e
    17/09/2026).
  - measures-loadprofiles: CONFERMATO su dati reali (issue #6, 17/09/2026)
    - la fonte primaria per questa integrazione.
  - history: forma ricavata dal bundle JS dell'app, MAI vista rispondere
    con dati reali. async_get_history può fallire o non contenere
    'podType' - il coordinator deve trattarlo come "meglio informato di
    niente", non come una fonte affidabile al 100% (vedi coordinator.py).
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import BASE_URL, HEADERS_BROWSER

_LOGGER = logging.getLogger(__name__)


class IretiApiError(Exception):
    """Chiamata fallita (trasporto, HTTP non-200, o risposta inattesa)."""


class IretiApiClient:
    def __init__(self, session: aiohttp.ClientSession, access_token: str) -> None:
        self._session = session
        self._headers = {**HEADERS_BROWSER, "Authorization": f"Bearer {access_token}"}

    async def _get(self, path: str) -> Any:
        try:
            async with self._session.get(f"{BASE_URL}{path}", headers=self._headers) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise IretiApiError(f"Errore di trasporto chiamando GET {path}: {err}") from err

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            async with self._session.post(
                f"{BASE_URL}{path}", json=payload, headers=self._headers
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise IretiApiError(f"Errore di trasporto chiamando POST {path}: {err}") from err

    async def async_get_company_id(self) -> str:
        """GET /users/public/company-by-host - idCompany per questo host
        (smartpod.ireti.it), serve per async_get_pods."""
        corpo = await self._get("/users/public/company-by-host")
        id_company = corpo.get("idCompany") if isinstance(corpo, dict) else None
        if not id_company:
            raise IretiApiError(f"'idCompany' mancante nella risposta company-by-host: {corpo!r}")
        return id_company

    async def async_get_consumer(self, username: str) -> dict[str, Any]:
        """GET /users/consumer/getbykeycloakusername/{username} - anagrafica
        del consumer loggato: idConsumer, codFiscale/pIVA (servono alle
        chiamate di misura), elenco pods grezzo (non usato qui, si usa
        async_get_pods che è la fonte confermata più completa)."""
        corpo = await self._get(f"/users/consumer/getbykeycloakusername/{username}")
        modelli = corpo.get("entityModel") if isinstance(corpo, dict) else None
        if not modelli:
            raise IretiApiError(f"Nessun consumer trovato per l'utente {username!r}: {corpo!r}")
        return modelli[0]

    async def async_get_pods(self, id_consumer: str, id_company: str) -> list[dict[str, Any]]:
        """GET /users/pods/getallbyconsumerandcompany/{id_consumer}/{id_company}

        Lista vuota (non un errore) se l'account non ha ancora nessun POD
        associato su questa società - stato normale per un account appena
        creato, va segnalato all'utente in config_flow con un messaggio
        chiaro ("associa il POD dal portale"), non trattato come un
        fallimento dell'integrazione."""
        corpo = await self._get(
            f"/users/pods/getallbyconsumerandcompany/{id_consumer}/{id_company}"
        )
        modelli = corpo.get("entityModel") if isinstance(corpo, dict) else None
        return list(modelli or [])

    async def async_get_history(self, pod: str, customer_tax_code_vat: str,
                                 start_iso: str, end_iso: str) -> dict[str, Any]:
        """POST /users/exabeat/history - NON confermato su dati reali (vedi
        docstring del modulo). Ritorna il corpo grezzo: è compito del
        chiamante decidere cosa fare se 'podType' non c'è."""
        return await self._post(
            "/users/exabeat/history",
            {
                "customerTaxCodeVat": customer_tax_code_vat,
                "pod": pod,
                "startDate": start_iso,
                "endDate": end_iso,
            },
        )

    async def async_get_measures_loadprofiles(
        self, pod: str, customer_tax_code_vat: str, pod_type: str,
        start_iso: str, end_iso: str,
    ) -> list[dict[str, Any]]:
        """POST /readings/exabeat/measures-loadprofiles - CONFERMATO su dati
        reali (issue #6): curva di carico a 15 minuti, un elemento
        loadProfiles per ogni giorno disponibile nel range richiesto.

        Ritorna la lista 'loadProfiles' (vuota se non c'è ancora nulla per
        il range richiesto - stato normale, non un errore)."""
        corpo = await self._post(
            "/readings/exabeat/measures-loadprofiles",
            {
                "operation": "PRELIEVO",
                "podType": pod_type,
                "startDate": start_iso,
                "endDate": end_iso,
                "customerTaxCodeVat": customer_tax_code_vat,
                "pod": pod,
            },
        )
        if not isinstance(corpo, dict):
            raise IretiApiError(f"Risposta inattesa da measures-loadprofiles: {corpo!r}")
        return list(corpo.get("loadProfiles") or [])
