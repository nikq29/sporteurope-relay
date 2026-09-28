from relay.web import create_app
from stubs import StubClient, StubRelay


async def test_real_page_and_assets_are_served_locally(aiohttp_client):
    tv = await aiohttp_client(create_app(StubClient(), StubRelay()))
    page = await (await tv.get("/")).text()
    assert '<script src="/static/hls.min.js"></script>' in page
    assert "cdn" not in page.lower()
    for name in ("app.js", "style.css", "hls.min.js"):
        resp = await tv.get(f"/static/{name}")
        assert resp.status == 200, name
    assert "Hls" in await (await tv.get("/static/hls.min.js")).text()
