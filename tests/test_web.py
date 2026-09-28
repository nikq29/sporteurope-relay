import pytest

from relay.sporteurope_client import DrmProtected, LoginFailed, NotPurchased
from relay.web import create_app
from stubs import StubClient, StubRelay, make_game

LIVE = make_game("live-1")
UPCOMING = make_game("soon-1", live=False)
LOCKED = make_game("locked-1", unlocked=False)


@pytest.fixture
def relay():
    return StubRelay()


@pytest.fixture
def static_dir(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Relay</title>")
    return tmp_path


@pytest.fixture
async def tv(aiohttp_client, relay, static_dir):
    return await aiohttp_client(create_app(StubClient([LIVE, UPCOMING, LOCKED]), relay, static_dir))


async def test_index_is_served(tv):
    resp = await tv.get("/")
    assert resp.status == 200 and "<title>Relay</title>" in await resp.text()


async def test_games_lists_json(tv):
    data = await (await tv.get("/api/games")).json()
    assert [g["id"] for g in data["games"]] == ["live-1", "soon-1", "locked-1"]
    assert data["error"] is None


async def test_games_reports_login_failure_in_german(aiohttp_client, relay, static_dir):
    tv = await aiohttp_client(create_app(StubClient(error=LoginFailed()), relay, static_dir))
    data = await (await tv.get("/api/games")).json()
    assert data["games"] == []
    assert data["message"] == "Login fehlgeschlagen – Zugangsdaten in HA prüfen"


async def test_play_starts_relay(tv, relay):
    resp = await tv.post("/api/play", json={"game_id": "live-1"})
    assert resp.status == 200
    assert (await resp.json())["state"] == "live"
    assert relay.started == ["live-1"]


async def test_second_tv_joins_running_game_without_restart(tv, relay):
    await tv.post("/api/play", json={"game_id": "live-1"})
    resp = await tv.post("/api/play", json={"game_id": "live-1"})
    assert resp.status == 200
    assert relay.started == ["live-1"]


@pytest.mark.parametrize("game_id,status,message", [
    ("soon-1", 409, "Spiel ist noch nicht live"),
    ("nope", 404, "Spiel nicht gefunden"),
])
async def test_play_refuses(tv, relay, game_id, status, message):
    resp = await tv.post("/api/play", json={"game_id": game_id})
    assert resp.status == status
    assert (await resp.json())["message"] == message
    assert relay.started == []


async def test_play_reports_drm_in_german(tv, relay):
    relay.fail = DrmProtected()
    resp = await tv.post("/api/play", json={"game_id": "live-1"})
    assert resp.status == 403
    assert (await resp.json())["message"] == "Dieses Spiel ist DRM-geschützt – nicht unterstützt"


async def test_status_includes_message(tv, relay):
    relay.state, relay.error = "error", "stream_in_use"
    data = await (await tv.get("/api/status")).json()
    assert data["message"] == "Stream wird an anderer Stelle genutzt"


async def test_stop(tv, relay):
    await tv.post("/api/play", json={"game_id": "live-1"})
    assert (await (await tv.post("/api/stop")).json())["state"] == "idle"


async def test_live_playlist_unavailable_without_game(tv, relay):
    resp = await tv.get("/live.m3u8")
    assert resp.status == 503 and resp.headers["Retry-After"] == "2"
    assert relay.touched == ["127.0.0.1"]


async def test_live_playlist_and_segments(tv, relay):
    relay.text = "#EXTM3U\n"
    relay.segments = {7: b"ts-bytes"}
    resp = await tv.get("/live.m3u8")
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("application/vnd.apple.mpegurl")
    seg = await tv.get("/seg/7.ts")
    assert seg.status == 200 and await seg.read() == b"ts-bytes"
    assert seg.headers["Content-Type"] == "video/mp2t"
    assert (await tv.get("/seg/8.ts")).status == 404


async def test_game_marked_locked_is_still_tried_because_it_may_have_been_bought_since(tv, relay):
    resp = await tv.post("/api/play", json={"game_id": "locked-1"})
    assert resp.status == 200
    assert relay.started == ["locked-1"]


async def test_really_unpurchased_game_reports_lock(tv, relay):
    relay.fail = NotPurchased()
    resp = await tv.post("/api/play", json={"game_id": "locked-1"})
    assert resp.status == 403
    assert (await resp.json())["message"] == "🔒 Nicht gekauft"
