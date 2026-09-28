"""Mirrors the relay state into sensor.sporteurope_relay via the Supervisor's Core API proxy."""
import asyncio
import logging

import aiohttp

SENSOR_URL = "http://supervisor/core/api/states/sensor.sporteurope_relay"

log = logging.getLogger(__name__)


class HaStatus:
    def __init__(self, http: aiohttp.ClientSession | None, token: str | None, relay, *,
                 url: str = SENSOR_URL, interval: float = 10.0):
        self._http = http
        self._token = token
        self._relay = relay
        self._url = url
        self._interval = interval
        self._last: dict | None = None

    def payload(self) -> dict:
        status = self._relay.status()
        state = status["state"] if status["state"] in ("live", "error") else "idle"
        game = status["game"]
        return {
            "state": state,
            "attributes": {
                "friendly_name": "Sporteurope Relay",
                "icon": "mdi:hockey-puck",
                "game": game["name"] if game else None,
                "viewers": status["viewers"],
                "error": status["error"],
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
        while True:
            await self.publish_once()
            await asyncio.sleep(self._interval)
