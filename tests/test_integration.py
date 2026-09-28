import asyncio

from fakes import EMAIL, LIVE_ID, OWNED_PRODUCT, PASSWORD, asset
from relay.app import build_app
from relay.config import Config


async def test_three_tvs_share_one_upstream_and_relay_stops_when_idle(fake, aiohttp_client):
    fake.add_asset(asset(LIVE_ID, "kassel-live", live=True), [OWNED_PRODUCT])
    app = await build_app(Config(email=EMAIL, password=PASSWORD), base_url=fake.base_url,
                          relay_kwargs={"poll_interval": 0.05, "idle_timeout": 1.0})
    tv = await aiohttp_client(app)

    resp = await tv.post("/api/play", json={"game_id": LIVE_ID})
    assert resp.status == 200, await resp.text()

    received = [{}, {}, {}]

    async def watch(store):
        for _ in range(30):
            playlist = await (await tv.get("/live.m3u8")).text()
            for line in playlist.splitlines():
                if line.startswith("seg/") and line not in store:
                    store[line] = await (await tv.get("/" + line)).read()
            await asyncio.sleep(0.05)

    async def live_edge():
        for _ in range(10):
            await asyncio.sleep(0.12)
            fake.advance(1)

    await asyncio.gather(*(watch(store) for store in received), live_edge())

    assert len(fake.segment_fetches) >= 10
    assert set(fake.segment_fetches.values()) == {1}, "every upstream segment fetched exactly once"
    assert fake.calls["login"] == 1
    for store in received:
        assert len(store) >= 8
        assert all(data.startswith(b"seg-1080-") for data in store.values())
    assert received[0]["seg/0.ts"] == received[1]["seg/0.ts"] == received[2]["seg/0.ts"]

    await asyncio.sleep(1.5)  # no TV asks anymore
    status = await (await tv.get("/api/status")).json()
    assert status["state"] == "idle"
    polls = fake.calls["rendition"]
    await asyncio.sleep(0.3)
    assert fake.calls["rendition"] == polls
