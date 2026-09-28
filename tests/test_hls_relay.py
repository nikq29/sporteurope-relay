import asyncio

import pytest

from fakes import LIVE_ID
from relay.hls_relay import HlsRelay
from relay.sporteurope_client import DrmProtected
from stubs import make_game

FAST = {"poll_interval": 0.02}


async def until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


@pytest.fixture
async def relay(client, http):
    r = HlsRelay(client, http, idle_timeout=5.0, **FAST)
    yield r
    await r.stop()


async def test_start_fetches_only_newest_segments_of_best_variant(fake, relay):
    fake.live_segments = 20  # long DVR window
    await relay.start(make_game(LIVE_ID))
    assert relay.state == "live"
    assert sorted(fake.segment_fetches) == [("1080", 117), ("1080", 118), ("1080", 119)]
    text = relay.playlist_text()
    assert text.count("seg/") == 3
    assert "mux" not in text and "signature" not in text


async def test_max_height_limits_variant(fake, client, http):
    relay = HlsRelay(client, http, max_height=720, **FAST)
    await relay.start(make_game(LIVE_ID))
    await relay.stop()
    assert {height for height, _ in fake.segment_fetches} == {"540"}


async def test_each_upstream_segment_is_fetched_once(fake, relay):
    await relay.start(make_game(LIVE_ID))
    for _ in range(5):
        fake.advance(1)
        newest = fake.media_seq + fake.live_segments - 1
        await until(lambda: ("1080", newest) in fake.segment_fetches)
    await asyncio.sleep(0.1)
    assert set(fake.segment_fetches.values()) == {1}
    assert len(fake.segment_fetches) == 3 + 5


async def test_serves_buffered_segments(fake, relay):
    await relay.start(make_game(LIVE_ID))
    assert relay.segment(0) == b"seg-1080-101"
    assert relay.segment(2) == b"seg-1080-103"
    assert relay.segment(99) is None


async def test_drm_token_is_refused_before_touching_mux(fake, relay):
    fake.drm_token = "drm-jwt"
    with pytest.raises(DrmProtected):
        await relay.start(make_game(LIVE_ID))
    assert (relay.state, relay.error) == ("error", "drm")
    assert fake.calls["master"] == 0 and not fake.segment_fetches


async def test_encrypted_rendition_is_refused(fake, relay):
    fake.rendition_key = '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://k",KEYFORMAT="com.apple.streamingkeydelivery"'
    with pytest.raises(DrmProtected):
        await relay.start(make_game(LIVE_ID))
    assert not fake.segment_fetches
    assert relay.playlist_text() is None


async def test_expired_mux_token_refreshes_stream_info_once(fake, relay):
    await relay.start(make_game(LIVE_ID))
    before = fake.calls["stream_info"]
    fake.mux_forbidden = 1
    fake.advance(1)
    await until(lambda: ("1080", 104) in fake.segment_fetches)
    assert fake.calls["stream_info"] == before + 1
    assert relay.state == "live"


async def test_repeated_mux_rejection_stops_without_fighting(fake, relay):
    await relay.start(make_game(LIVE_ID))
    fake.mux_forbidden = 2
    await until(lambda: relay.state == "error")
    assert relay.error == "stream_in_use"
    polls = fake.calls["rendition"]
    await asyncio.sleep(0.2)
    assert fake.calls["rendition"] == polls


async def test_lost_access_on_refresh_is_stream_in_use(fake, client, http):
    relay = HlsRelay(client, http, refresh_interval=0.05, **FAST)
    await relay.start(make_game(LIVE_ID))
    fake.stream_status = 403
    await until(lambda: relay.state == "error")
    assert relay.error == "stream_in_use"
    await relay.stop()


async def test_upstream_outage_gives_up_after_max_outage(fake, client, http):
    relay = HlsRelay(client, http, max_outage=0.0, **FAST)
    await relay.start(make_game(LIVE_ID))
    fake.rendition_status = 500
    await until(lambda: relay.state == "error", timeout=5.0)
    assert relay.error == "upstream"
    await relay.stop()


async def test_idle_timeout_releases_upstream(fake, client, http):
    relay = HlsRelay(client, http, idle_timeout=0.2, **FAST)
    await relay.start(make_game(LIVE_ID))
    await until(lambda: relay.state == "idle")
    assert relay.game is None and relay.playlist_text() is None
    polls = fake.calls["rendition"]
    await asyncio.sleep(0.1)
    assert fake.calls["rendition"] == polls


async def test_viewer_requests_keep_relay_alive(fake, client, http):
    relay = HlsRelay(client, http, idle_timeout=0.2, **FAST)
    await relay.start(make_game(LIVE_ID))
    for _ in range(10):
        relay.touch("192.168.0.50")
        await asyncio.sleep(0.05)
    assert relay.state == "live"
    assert relay.viewers() == 1
    assert relay.status()["game"]["id"] == LIVE_ID
    await relay.stop()


async def test_sequence_regression_restarts_buffer_with_discontinuity(fake, relay):
    await relay.start(make_game(LIVE_ID))
    fake.media_seq = 5  # encoder restart: upstream numbering drops
    await until(lambda: ("1080", 8) in fake.segment_fetches)
    text = relay.playlist_text()
    assert "#EXT-X-MEDIA-SEQUENCE:3\n#EXT-X-DISCONTINUITY-SEQUENCE:1\n" in text
    assert relay.segment(3) == b"seg-1080-6"


async def test_switching_game_keeps_local_numbering(fake, relay):
    await relay.start(make_game(LIVE_ID))
    await relay.start(make_game(LIVE_ID, name="Anderes Spiel"))
    text = relay.playlist_text()
    assert "#EXT-X-MEDIA-SEQUENCE:3\n#EXT-X-DISCONTINUITY-SEQUENCE:1\n" in text
