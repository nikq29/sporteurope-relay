"""Mirrors the relay state into sensor.sporteurope_relay via the Supervisor's Core API proxy."""
import asyncio
import logging
import time

import aiohttp

from relay.sporteurope_client import SporteuropeError
from relay.web import status_message

SENSOR_URL = "http://supervisor/core/api/states/sensor.sporteurope_relay"

log = logging.getLogger(__name__)


class HaStatus:
    def __init__(self, http: aiohttp.ClientSession | None, token: str | None, relay, *, client=None,
                 url: str = SENSOR_URL, interval: float = 10.0, check_interval: float = 1800.0):
        self._http = http
        self._client = client
        self._check_interval = check_interval
        self._token = token
        self._relay = relay
        self._url = url
        self._interval = interval
        self._last: dict | None = None

    def payload(self) -> dict:
        status = self._relay.status()
        if self._client is not None and self._client.login_failed and status["state"] != "live":
            status = {**status, "state": "error", "error": "login_failed"}
        state = status["state"] if status["state"] in ("live", "ended", "error") else "idle"
        game = status["game"]
        return {
            "state": state,
            "attributes": {
                "friendly_name": "Sporteurope Relay",
                "icon": "mdi:hockey-puck",
                "game": game["name"] if game else None,
                "viewers": status["viewers"],
                "error": status["error"],
                "message": status_message(status),
            },
        }

    async def publish_once(self) -> bool:
        if not self._token or self._http is None:
            return False
        payload = self.payload()
        if payload == self._last:
            return False
        try:
            async with self._http.post(self._url, json=payload,
                                       headers={"Authorization": f"Bearer {self._token}"}) as resp:
                if resp.status >= 400:
                    log.warning("Home Assistant rejected sensor update: HTTP %s", resp.status)
                    return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Home Assistant not reachable: %s", type(exc).__name__)
            return False
        self._last = payload
        return True

    async def run(self) -> None:
        last_check: float | None = None
        while True:
            try:
                now = time.monotonic()
                if self._client is not None and (last_check is None or now - last_check >= self._check_interval):
                    # Log in and load the games well before kickoff, so bad credentials show up early.
                    last_check = now
                    try:
                        await self._client.list_games()
                    except SporteuropeError as exc:
                        log.warning("Background check failed: %s", exc)
                await self.publish_once()
            except Exception:
                log.exception("Sensor update failed")
            await asyncio.sleep(self._interval)
