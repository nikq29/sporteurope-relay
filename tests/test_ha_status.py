import aiohttp
from aiohttp import web

from relay.ha_status import HaStatus
from stubs import StubRelay, make_game

PATH = "/core/api/states/sensor.sporteurope_relay"


async def fake_ha(aiohttp_server):
    posts = []

    async def handler(request):
        posts.append((request.headers.get("Authorization"), await request.json()))
        return web.json_response({})

    app = web.Application()
    app.router.add_post(PATH, handler)
    server = await aiohttp_server(app)
    return str(server.make_url(PATH)), posts


async def test_publishes_state_once_per_change(aiohttp_server):
    url, posts = await fake_ha(aiohttp_server)
    relay = StubRelay()
    async with aiohttp.ClientSession() as http:
        ha = HaStatus(http, "tok", relay, url=url)
        assert await ha.publish_once() is True
        assert await ha.publish_once() is False
        relay.game, relay.state = make_game("g1", name="Kassel – Crimmitschau"), "live"
        relay.touched = ["192.168.0.50", "192.168.0.51"]
        assert await ha.publish_once() is True
    assert len(posts) == 2
    auth, body = posts[1]
    assert auth == "Bearer tok"
    assert body["state"] == "live"
    assert body["attributes"]["game"] == "Kassel – Crimmitschau"
    assert body["attributes"]["viewers"] == 2
    assert posts[0][1]["state"] == "idle"


async def test_starting_is_reported_as_idle():
    relay = StubRelay()
    relay.state = "starting"
    assert HaStatus(None, "tok", relay).payload()["state"] == "idle"


async def test_without_supervisor_token_nothing_is_sent():
    assert await HaStatus(None, None, StubRelay()).publish_once() is False


async def test_ha_errors_do_not_raise(aiohttp_server):
    async with aiohttp.ClientSession() as http:
        ha = HaStatus(http, "tok", StubRelay(), url="http://127.0.0.1:9/nothing")
        assert await ha.publish_once() is False
