"""Signed stream links let AirPlay/Chromecast receivers fetch the stream without the password."""
import asyncio
import base64
from urllib.parse import parse_qs, urlsplit

import pytest

from relay.web import create_app
from stubs import StubClient, StubRelay, make_game

TUNNEL = {"Cf-Connecting-Ip": "203.0.113.7", "Cf-Ray": "abc-FRA", "X-Forwarded-Proto": "https",
          "Host": "huskies.example.de"}
AUTH = {"Authorization": "Basic " + base64.b64encode(b"tv:geheim").decode()}


@pytest.fixture
def static_dir(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>")
    return tmp_path


async def make_tv(aiohttp_client, static_dir, **kwargs):
    relay = StubRelay()
    relay.text = "#EXTM3U\n#EXTINF:4.000,\nseg/1.ts\n#EXTINF:4.000,\nseg/2.ts\n"
    relay.segments = {1: b"one", 2: b"two"}
    app = create_app(StubClient([make_game("live-1")]), relay, static_dir, remote_password="geheim", **kwargs)
    return await aiohttp_client(app)


async def stream_url(tv, headers):
    resp = await tv.get("/api/stream-url", headers=headers)
    assert resp.status == 200
    return (await resp.json())["url"]


def token_of(url):
    return parse_qs(urlsplit(url).query)["t"][0]


async def test_stream_url_through_tunnel_is_public_https_with_token(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir)
    url = await stream_url(tv, {**TUNNEL, **AUTH})
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "huskies.example.de", "/live.m3u8")
    assert token_of(url)


async def test_stream_url_needs_login_through_tunnel(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir)
    assert (await tv.get("/api/stream-url", headers=TUNNEL)).status == 401


async def test_stream_url_on_lan_is_plain_http(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir)
    url = await stream_url(tv, {})
    assert url.startswith("http://127.0.0.1:") and "/live.m3u8?t=" in url


async def test_receiver_plays_through_tunnel_with_token_only(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir)
    t = token_of(await stream_url(tv, {**TUNNEL, **AUTH}))
    resp = await tv.get(f"/live.m3u8?t={t}", headers=TUNNEL)
    assert resp.status == 200
    text = await resp.text()
    assert f"seg/1.ts?t={t}" in text and f"seg/2.ts?t={t}" in text
    seg = await tv.get(f"/seg/2.ts?t={t}", headers=TUNNEL)
    assert seg.status == 200 and await seg.read() == b"two"


async def test_token_does_not_unlock_the_api(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir)
    t = token_of(await stream_url(tv, {**TUNNEL, **AUTH}))
    assert (await tv.get(f"/api/games?t={t}", headers=TUNNEL)).status == 401
    assert (await tv.post(f"/api/play?t={t}", json={"game_id": "live-1"}, headers=TUNNEL)).status == 401
    assert (await tv.get(f"/?t={t}", headers=TUNNEL)).status == 401


@pytest.mark.parametrize("bad", ["", "kaputt", "9999999999.0000", "1.abc"])
async def test_forged_tokens_are_rejected(aiohttp_client, static_dir, bad):
    tv = await make_tv(aiohttp_client, static_dir)
    assert (await tv.get(f"/live.m3u8?t={bad}", headers=TUNNEL)).status == 401


async def test_expired_token_is_rejected(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir, stream_token_ttl=1)
    t = token_of(await stream_url(tv, {**TUNNEL, **AUTH}))
    assert (await tv.get(f"/live.m3u8?t={t}", headers=TUNNEL)).status == 200
    await asyncio.sleep(2.1)
    assert (await tv.get(f"/live.m3u8?t={t}", headers=TUNNEL)).status == 401


async def test_token_from_another_relay_instance_is_rejected(aiohttp_client, static_dir):
    tv1 = await make_tv(aiohttp_client, static_dir)
    tv2 = await make_tv(aiohttp_client, static_dir)
    t = token_of(await stream_url(tv1, {**TUNNEL, **AUTH}))
    assert (await tv2.get(f"/live.m3u8?t={t}", headers=TUNNEL)).status == 401


async def test_stream_responses_allow_cast_receivers_cross_origin(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir)
    t = token_of(await stream_url(tv, {**TUNNEL, **AUTH}))
    for path in (f"/live.m3u8?t={t}", f"/seg/1.ts?t={t}"):
        resp = await tv.get(path, headers=TUNNEL)
        assert resp.headers.get("Access-Control-Allow-Origin") == "*", path


async def test_lan_playlist_without_token_is_unchanged(aiohttp_client, static_dir):
    tv = await make_tv(aiohttp_client, static_dir)
    text = await (await tv.get("/live.m3u8")).text()
    assert "seg/1.ts\n" in text and "?t=" not in text
