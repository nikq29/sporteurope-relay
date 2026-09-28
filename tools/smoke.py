"""Manual check against the real Sporteurope account. Prints no password, tokens or signed URLs.

Usage:
  SPORTEUROPE_EMAIL=... SPORTEUROPE_PASSWORD=... .venv/bin/python tools/smoke.py [--asset-id ID] [--shapes]
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sporteurope_relay"))

import aiohttp  # noqa: E402

from relay import playlist  # noqa: E402
from relay.logsafe import redact_url, setup_logging  # noqa: E402
from relay.sporteurope_client import SporteuropeClient  # noqa: E402


def shape(value, depth=0):
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"… {len(value)} items"] if value else []
    return type(value).__name__ if value is not None else None


async def main(args):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
        client = SporteuropeClient(http, os.environ["SPORTEUROPE_EMAIL"], os.environ["SPORTEUROPE_PASSWORD"], args.team)
        await client.login()
        print(f"owned product/asset ids: {len(client.owned_ids)}")
        if args.shapes:
            print(json.dumps(shape(client.login_body), indent=1))
        for game in await client.list_games():
            when = "LIVE" if game.live else (game.start.strftime("%a %d.%m. %H:%M UTC") if game.start else "?")
            lock = "unlocked" if game.unlocked else "NOT PURCHASED"
            print(f"{when:22} {lock:14} {game.home} – {game.guest}  id={game.id}")
        if args.asset_id:
            info = await client.stream_info(args.asset_id)
            print("tokens.drm set:", info.drm)
            async with http.get(info.master_url) as resp:
                master = await resp.text()
            print("master has DRM tags:", playlist.has_drm(master))
            variant = playlist.pick_variant(playlist.parse_master(master, info.master_url), 1080)
            print("chosen variant:", variant.height, redact_url(variant.uri))
            async with http.get(variant.uri) as resp:
                rendition = await resp.text()
            print("rendition has DRM tags:", playlist.has_drm(rendition))
            print("live playlist:", "#EXT-X-ENDLIST" not in rendition, "| segments:", rendition.count("#EXTINF"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", default="ec-kassel-huskies")
    parser.add_argument("--asset-id")
    parser.add_argument("--shapes", action="store_true")
    setup_logging()
    asyncio.run(main(parser.parse_args()))
