"""Wires client, relay, web routes and the HA sensor into one aiohttp app."""
import asyncio
import logging
import os
from contextlib import suppress

import aiohttp
from aiohttp import web

from relay.config import OPTIONS_PATH, Config, load_config
from relay.ha_status import HaStatus
from relay.hls_relay import HlsRelay
from relay.logsafe import setup_logging
from relay.sporteurope_client import API_BASE, SporteuropeClient
from relay.web import create_app

PORT = 8099

log = logging.getLogger(__name__)


async def build_app(cfg: Config, *, base_url: str = API_BASE, ha_token: str | None = None,
                    relay_kwargs: dict | None = None) -> web.Application:
    http = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),
                                 timeout=aiohttp.ClientTimeout(total=20))
    client = SporteuropeClient(http, cfg.email, cfg.password, cfg.team_slug, base_url=base_url)
    relay = HlsRelay(client, http, max_height=cfg.max_height, **(relay_kwargs or {}))
    ha = HaStatus(http, ha_token, relay, client=client)
    app = create_app(client, relay, remote_password=cfg.remote_password)

    tasks: list[asyncio.Task] = []

    async def on_startup(app):
        tasks.append(asyncio.create_task(ha.run()))

    async def on_cleanup(app):
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await relay.stop()
        await http.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    setup_logging()
    cfg = load_config(os.environ.get("RELAY_OPTIONS", OPTIONS_PATH))
    log.info("Sporteurope Relay for team %s on port %d (max %dp)", cfg.team_slug, PORT, cfg.max_height)
    web.run_app(build_app(cfg, ha_token=os.environ.get("SUPERVISOR_TOKEN")), port=PORT, access_log=None,
                print=None)
