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


def test_payload_carries_german_message_and_ended_state():
    relay = StubRelay()
    relay.state, relay.error = "error", "stream_in_use"
    attrs = HaStatus(None, "tok", relay).payload()["attributes"]
    assert attrs["message"] == "Stream wird an anderer Stelle genutzt"
    relay.state, relay.error = "ended", None
    payload = HaStatus(None, "tok", relay).payload()
    assert payload["state"] == "ended" and payload["attributes"]["message"] == "Spiel beendet"


def test_login_failure_shows_as_error_on_the_sensor():
    from relay.sporteurope_client import LoginFailed
    from stubs import StubClient
    payload = HaStatus(None, "tok", StubRelay(), client=StubClient(error=LoginFailed())).payload()
    assert payload["state"] == "error"
    assert payload["attributes"]["error"] == "login_failed"
    assert payload["attributes"]["message"] == "Login fehlgeschlagen – Zugangsdaten in HA prüfen"


async def test_run_checks_login_periodically_and_survives_errors(aiohttp_server):
    import asyncio
    from relay.sporteurope_client import UpstreamError
    from stubs import StubClient
    url, posts = await fake_ha(aiohttp_server)
    relay = StubRelay()
    client = StubClient(error=UpstreamError("down"))
    calls = {"n": 0}
    original = relay.status

    def flaky_status():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bug")
        return original()

    relay.status = flaky_status
    async with aiohttp.ClientSession() as http:
        ha = HaStatus(http, "tok", relay, client=client, url=url, interval=0.02, check_interval=0.05)
        task = asyncio.create_task(ha.run())
        await asyncio.sleep(0.3)
        task.cancel()
    assert posts, "kept publishing after an unexpected error"
    assert client.calls >= 3, "login/game check repeats"
