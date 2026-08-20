"""Client for the e-Distribuzione data APIs (xs-misura-p.de-c1.eu1.cloudhub.io).

This half is a normal, stable-ish JSON REST API behind a Bearer token - much
less scary than auth.py. The only quirk is the `Method_User` header, which the
backend uses as an application-level permission discriminator per endpoint.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import aiohttp

from .const import (
    METHOD_USER_CURVA_GIORNO,
    METHOD_USER_CURVA_MESE,
    METHOD_USER_CURVA_PERIODO,
    METHOD_USER_ELENCO_POD,
    METHOD_USER_LETTURE,
    MISURE_DAILY_LOAD_PROFILE_URL,
    MISURE_GET_SUPPLIES_URL,
    MISURE_MONTHLY_LOAD_PROFILE_URL,
    MISURE_MONTHLY_TIME_OF_USE_URL,
    MISURE_READING_URL,
)

_LOGGER = logging.getLogger(__name__)


class EdistribuzioneApiError(Exception):
    """Raised when the API returns a non-OK meta.status or a transport error."""


class EdistribuzioneApiClient:
    """Thin wrapper around the misure endpoints. Auth/refresh is handled
    upstream by the coordinator, which hands us a live access_token."""

    def __init__(self, session: aiohttp.ClientSession, access_token: str) -> None:
        self._session = session
        self._access_token = access_token

    def update_token(self, access_token: str) -> None:
        self._access_token = access_token

    def _headers(self, method_user: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Method_User": method_user,
            "Accept": "*/*",
            # A generic user agent is fine; the Method_User header is what the
            # backend actually keys permissions on, not the UA string.
            "User-Agent": "HomeAssistant-edistribuzione",
            "client_id": "",
            "client_secret": "",
        }

    async def _get_json(self, url: str, method_user: str, params: dict) -> dict:
        async with self._session.get(
            url, headers=self._headers(method_user), params=params
        ) as resp:
            if resp.status == 401:
                raise EdistribuzioneApiError("401 Unauthorized - access_token expired")
            resp.raise_for_status()
            payload = await resp.json(content_type=None)

        meta = payload.get("meta", {})
        if meta.get("status") not in ("OK", None):
            raise EdistribuzioneApiError(f"API returned meta.status={meta.get('status')}: {meta}")
        return payload

    async def async_get_supplies(self) -> list[dict[str, Any]]:
        """List all PODs (delivery points) associated with the account."""
        # json={} invece di headers manuali con corpo assente: il backend
        # e' MuleSoft (cloudhub.io), e con Content-Type: application/json
        # dichiarato ma nessun corpo, il parser lato server probabilmente
        # falliva silenziosamente restituendo un 500 generico
        # ("Generic Error", nessun dettaglio) invece di un errore di
        # validazione preciso - coerente con l'esito osservato dopo aver
        # aggiunto solo il Content-Type senza corpo. json={} fa si' che
        # aiohttp mandi un corpo vero ('{}') insieme all'header corretto,
        # invece dei due gestiti separatamente. Non confermato byte-per-byte
        # (nessuna HAR reale per questo endpoint), ma comportamento più
        # plausibile del precedente, che ha eliminato il 415 ma non il 500.
        async with self._session.post(
            MISURE_GET_SUPPLIES_URL,
            headers=self._headers(METHOD_USER_ELENCO_POD),
            json={},
        ) as resp:
            if resp.status >= 400:
                body_preview = await resp.text()
                _LOGGER.error(
                    "getSupplies ha risposto %s. Corpo (primi 500 caratteri): %r",
                    resp.status,
                    body_preview[:500],
                )
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
        try:
            return payload["data"][0]["pods"]
        except (KeyError, IndexError):
            return []

    async def async_get_reading(
        self, pod: str, date_from: date, date_to: date
    ) -> list[dict[str, Any]]:
        """Official monthly-published readings (EA/ER per fascia + POT peaks)."""
        params = {
            "pointofdelivery": pod,
            "rangeDateFrom": date_from.isoformat(),
            "rangeDateTo": date_to.isoformat(),
        }
        payload = await self._get_json(MISURE_READING_URL, METHOD_USER_LETTURE, params)
        return payload.get("data", [])

    async def async_get_daily_load_profile(
        self, pod: str, date_from: date, date_to: date | None = None, magnitude: str = "A1"
    ) -> list[dict[str, Any]]:
        """Hourly/quarter-hourly load curve.

        Se date_to è None (comportamento di prima, retrocompatibile),
        richiede un solo giorno (date_from == date_to nella richiesta).

        Se date_to è specificato e diverso da date_from, richiede un
        intervallo vero. NON ANCORA CONFERMATO se l'endpoint supporta
        davvero un range multi-giorno in un'unica risposta (rangeDateFrom/
        rangeDateTo erano finora sempre passati uguali) o se lo tronca/
        ignora silenziosamente restituendo comunque un solo giorno - da
        verificare con verify_edistribuzione_login.py prima di farci
        affidamento in async_recupera_storico.
        """
        if date_to is None:
            date_to = date_from
        params = {
            "pointofdelivery": pod,
            "rangeDateFrom": date_from.isoformat(),
            "rangeDateTo": date_to.isoformat(),
            "magnitude": magnitude,
        }
        payload = await self._get_json(
            MISURE_DAILY_LOAD_PROFILE_URL, METHOD_USER_CURVA_GIORNO, params
        )
        return payload.get("data", [])

    async def async_get_monthly_load_profile(
        self, date_from: date, date_to: date, pod: str, magnitude: str = "A1"
    ) -> list[dict[str, Any]]:
        """Per-day consumption total across a date range."""
        params = {
            "pointofdelivery": pod,
            "rangeDateFrom": date_from.isoformat(),
            "rangeDateTo": date_to.isoformat(),
            "magnitude": magnitude,
        }
        payload = await self._get_json(
            MISURE_MONTHLY_LOAD_PROFILE_URL, METHOD_USER_CURVA_MESE, params
        )
        return payload.get("data", [])

    async def async_get_monthly_time_of_use(
        self, pod: str, date_from: date, date_to: date, magnitude: str = "A1"
    ) -> list[dict[str, Any]]:
        """Per-month consumption split by fascia (T1-T4) + power peaks."""
        params = {
            "pointofdelivery": pod,
            "rangeDateFrom": date_from.isoformat(),
            "rangeDateTo": date_to.isoformat(),
            "magnitude": magnitude,
        }
        payload = await self._get_json(
            MISURE_MONTHLY_TIME_OF_USE_URL, METHOD_USER_CURVA_PERIODO, params
        )
        return payload.get("data", [])
