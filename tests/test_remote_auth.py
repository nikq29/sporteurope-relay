"""Requests arriving through the Cloudflare tunnel need the remote password; the home LAN does not."""
import base64

import pytest

from relay.web import MAX_AUTH_FAILURES, create_app
from stubs import StubClient, StubRelay, make_game

TUNNEL = {"Cf-Connecting-Ip": "203.0.113.7", "Cf-Ray": "abc-FRA"}


def basic(password, user="huskies"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def static_dir(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Relay</title>")
    return tmp_path


async def make_tv(aiohttp_client, static_dir, remote_password):
    relay = StubRelay()
    relay.text = "#EXTM3U\n"
    relay.segments = {1: b"ts"}
    app = create_app(StubClient([make_game("live-1")]), relay, static_dir, remote_password=remote_password)
    return await aiohttp_client(app)


async def test_lan_needs_no_password(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir, "geheim")
    for path in ("/", "/api/games", "/live.m3u8", "/seg/1.ts"):
        assert (await tv.get(path)).status == 200, path


async def test_tunnel_is_blocked_when_no_password_is_configured(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir, "")
    resp = await tv.get("/", headers={**TUNNEL, **basic("")})
    assert resp.status == 403


@pytest.mark.parametrize("path", ["/", "/api/games", "/api/status", "/live.m3u8", "/seg/1.ts", "/static/x.js"])
async def test_tunnel_without_credentials_gets_login_prompt(aiohttp_client, static_dir, path):
    tv = await make_tv(aiohttp_client, static_dir, "geheim")
    resp = await tv.get(path, headers=TUNNEL)
    assert resp.status == 401
    assert resp.headers["WWW-Authenticate"].startswith("Basic ")


async def test_tunnel_play_needs_credentials(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir, "geheim")
    assert (await tv.post("/api/play", json={"game_id": "live-1"}, headers=TUNNEL)).status == 401


async def test_tunnel_with_wrong_password_is_rejected(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir, "geheim")
    assert (await tv.get("/", headers={**TUNNEL, **basic("falsch")})).status == 401


async def test_tunnel_with_correct_password_works_for_any_user_name(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir, "geheim")
    for user in ("huskies", "vlc", ""):
        assert (await tv.get("/live.m3u8", headers={**TUNNEL, **basic("geheim", user)})).status == 200
    assert (await tv.get("/seg/1.ts", headers={**TUNNEL, **basic("geheim")})).status == 200


async def test_malformed_authorization_header_is_rejected(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir, "geheim")
    resp = await tv.get("/", headers={**TUNNEL, "Authorization": "Basic %%%nicht-base64"})
    assert resp.status == 401


async def test_repeated_wrong_passwords_lock_out_that_client(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir, "geheim")
    for _ in range(MAX_AUTH_FAILURES):
        await tv.get("/", headers={**TUNNEL, **basic("falsch")})
    assert (await tv.get("/", headers={**TUNNEL, **basic("geheim")})).status == 429
    other = {**TUNNEL, "Cf-Connecting-Ip": "198.51.100.9"}
    assert (await tv.get("/", headers={**other, **basic("geheim")})).status == 200
    assert (await tv.get("/")).status == 200  # home LAN unaffected
