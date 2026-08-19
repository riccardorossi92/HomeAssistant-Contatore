"""DataUpdateCoordinator for e-Distribuzione.

Handles: refreshing the access_token via the stored refresh_token before each
poll, then pulling the latest monthly reading + monthly time-of-use data for
the configured POD. Daily load-profile (high resolution) is deliberately not
polled on the default schedule - it's a separate, cheap on-demand fetch you
can wire into a service call if you want finer granularity without hammering
the backend hourly.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EdistribuzioneApiClient, EdistribuzioneApiError
from .auth import EdistribuzioneAuthClient, EdistribuzioneAuthError
from .const import CONF_POD, CONF_REFRESH_TOKEN, DEFAULT_UPDATE_INTERVAL_MINUTES
from ...const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class EdistribuzioneCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} (E-Distribuzione)",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
        )
        self.entry = entry
        self.pod: str = entry.data[CONF_POD]
        session = async_get_clientsession(hass)
        self._auth = EdistribuzioneAuthClient(session)
        self._api = EdistribuzioneApiClient(session, access_token="")

    async def _async_ensure_token(self) -> None:
        refresh_token = self.entry.data[CONF_REFRESH_TOKEN]
        try:
            tokens = await self._auth.async_refresh_access_token(refresh_token)
        except EdistribuzioneAuthError as err:
            # A failed refresh almost certainly means the refresh_token was
            # revoked (password change, Enel-side session cleanup, ...) and
            # the user needs to go through config_flow's re-auth again.
            raise UpdateFailed(f"Token refresh failed: {err}") from err

        self._api.update_token(tokens.access_token)

        if tokens.refresh_token != refresh_token:
            new_data = dict(self.entry.data)
            new_data[CONF_REFRESH_TOKEN] = tokens.refresh_token
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    async def _async_update_data(self) -> dict:
        await self._async_ensure_token()

        today = date.today()
        month_start = today.replace(day=1)
        six_months_ago = (month_start - timedelta(days=1)).replace(day=1)
        # Cheap approximation of "6 months back" without extra deps; good
        # enough for a rolling time-of-use window.
        for _ in range(4):
            six_months_ago = (six_months_ago - timedelta(days=1)).replace(day=1)

        try:
            reading = await self._api.async_get_reading(
                self.pod, today - timedelta(days=45), today
            )
            time_of_use = await self._api.async_get_monthly_time_of_use(
                self.pod, six_months_ago, today
            )
        except EdistribuzioneApiError as err:
            raise UpdateFailed(f"Error fetching e-Distribuzione data: {err}") from err

        return {
            "reading": reading,
            "time_of_use": time_of_use,
        }

    @property
    def api(self) -> EdistribuzioneApiClient:
        """Expose the API client for on-demand calls (e.g. a future service
        that fetches the daily load profile for a specific day)."""
        return self._api
