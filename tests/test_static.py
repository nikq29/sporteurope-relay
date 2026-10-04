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


async def test_page_title_is_configurable(aiohttp_client):
    tv = await aiohttp_client(create_app(StubClient(), StubRelay(), title="Eishockey bei Niko"))
    page = await (await tv.get("/")).text()
    assert "<title>Eishockey bei Niko</title>" in page
    assert "<h1>Eishockey bei Niko</h1>" in page
    assert "Huskies live" not in page


async def test_default_page_title(aiohttp_client):
    tv = await aiohttp_client(create_app(StubClient(), StubRelay()))
    assert "<h1>Huskies live</h1>" in await (await tv.get("/")).text()


async def test_page_title_is_html_escaped(aiohttp_client):
    tv = await aiohttp_client(create_app(StubClient(), StubRelay(), title='<script>x</script> & "Co"'))
    page = await (await tv.get("/")).text()
    assert "<script>x</script>" not in page
    assert "<h1>&lt;script&gt;x&lt;/script&gt; &amp; &quot;Co&quot;</h1>" in page
