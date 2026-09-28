import aiohttp
import pytest

from fakes import EMAIL, PASSWORD, TEAM, FakeSporteurope
from relay.sporteurope_client import SporteuropeClient


@pytest.fixture
async def fake(aiohttp_server):
    f = FakeSporteurope()
    server = await aiohttp_server(f.app)
    f.base_url = str(server.make_url("")).rstrip("/")
    return f


@pytest.fixture
async def http():
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    yield session
    await session.close()


@pytest.fixture
def client(fake, http):
    return SporteuropeClient(http, EMAIL, PASSWORD, TEAM, base_url=fake.base_url)
