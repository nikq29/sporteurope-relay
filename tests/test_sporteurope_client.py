import pytest

from fakes import (EMAIL, FREE_ID, LIVE_ID, LOCKED_ID, OTHER_PRODUCT, OTHER_TEAM_ID, OWNED_PRODUCT, TEAM,
                   UPCOMING_ID, asset)
from relay.sporteurope_client import LoginFailed, NotPurchased, SporteuropeClient, StreamInUse


def populate(fake):
    fake.add_asset(asset(UPCOMING_ID, "kassel-crimmitschau", start="2026-10-02T17:00:00Z"), [OWNED_PRODUCT])
    fake.add_asset(asset(LIVE_ID, "kassel-live", live=True, start="2026-09-28T17:00:00Z"), [OWNED_PRODUCT])
    fake.add_asset(asset(LOCKED_ID, "kassel-locked", start="2026-10-04T16:00:00Z"), [OTHER_PRODUCT])
    fake.add_asset(asset(FREE_ID, "kassel-free", monetizations=(), start="2026-10-05T16:00:00Z"))
    fake.add_asset(asset(OTHER_TEAM_ID, "other", home="a", guest="b"), [OWNED_PRODUCT])


async def test_list_games_filters_sorts_and_marks_unlock(fake, client):
    populate(fake)
    games = await client.list_games()
    assert [(g.id, g.live, g.unlocked) for g in games] == [
        (LIVE_ID, True, True), (UPCOMING_ID, False, True), (LOCKED_ID, False, False), (FREE_ID, False, True),
    ]
    assert fake.calls["login"] == 1
    assert fake.calls["detail"] == 3  # the free game needs no detail call


async def test_list_games_is_cached_for_many_tvs(fake, client):
    populate(fake)
    for _ in range(5):
        await client.list_games()
    assert fake.calls["list"] == 1
    assert fake.calls["detail"] == 3


async def test_list_games_follows_pagination(fake, client):
    populate(fake)
    client.PER_PAGE = 2
    assert len(await client.list_games()) == 4
    assert fake.calls["list"] == 3


async def test_wrong_password_fails_once_without_retry_loop(fake, http):
    populate(fake)
    bad = SporteuropeClient(http, EMAIL, "falsch", TEAM, base_url=fake.base_url)
    for _ in range(3):
        with pytest.raises(LoginFailed):
            await bad.list_games()
    assert fake.calls["login"] == 1


async def test_stream_info_returns_master_url_and_drm_flag(fake, client):
    info = await client.stream_info(LIVE_ID)
    assert info.master_url.startswith(fake.base_url + f"/mux/{LIVE_ID}.m3u8?token=")
    assert info.drm is False
    fake.drm_token = "drm-jwt"
    assert (await client.stream_info(LIVE_ID)).drm is True


async def test_expired_session_logs_in_again_once(fake, client):
    await client.stream_info(LIVE_ID)
    fake.session_expired = True
    await client.stream_info(LIVE_ID)
    assert fake.calls["login"] == 2


@pytest.mark.parametrize("status,error", [(403, NotPurchased), (402, NotPurchased), (409, StreamInUse), (429, StreamInUse)])
async def test_stream_info_maps_refusals(fake, client, status, error):
    fake.stream_status = status
    with pytest.raises(error):
        await client.stream_info(LIVE_ID)


async def test_concurrent_list_games_share_one_upstream_fetch(fake, client):
    import asyncio
    populate(fake)
    fake.list_delay = 0.05
    results = await asyncio.gather(*(client.list_games() for _ in range(3)))
    assert all(len(r) == 4 for r in results)
    assert fake.calls["list"] == 1
    assert fake.calls["detail"] == 3


async def test_failing_detail_marks_only_that_game_unknown(fake, client):
    populate(fake)
    fake.detail_status[LOCKED_ID] = 500
    games = {g.id: g.unlocked for g in await client.list_games()}
    assert games[LOCKED_ID] is None and games[UPCOMING_ID] is True
    client.GAMES_TTL = 0
    fake.detail_status.clear()
    games = {g.id: g.unlocked for g in await client.list_games()}
    assert games[LOCKED_ID] is False


async def test_list_failure_serves_last_good_list(fake, client):
    populate(fake)
    first = await client.list_games()
    client.GAMES_TTL = 0
    fake.list_status = 500
    assert await client.list_games() == first


async def test_list_failure_without_cache_is_not_retried_immediately(fake, client):
    from relay.sporteurope_client import UpstreamError
    populate(fake)
    fake.list_status = 500
    for _ in range(3):
        with pytest.raises(UpstreamError):
            await client.list_games()
    assert fake.calls["list"] == 1


async def test_team_profile_id_is_resolved_once(fake, client):
    populate(fake)
    client.GAMES_TTL = 0
    for _ in range(3):
        await client.list_games()
    assert fake.calls["profile_slug"] == 1
    assert fake.calls["list"] == 3


async def test_unknown_team_slug_fails_clearly(fake, http):
    from relay.sporteurope_client import UpstreamError
    populate(fake)
    other = SporteuropeClient(http, EMAIL, "richtig", "gibt-es-nicht", base_url=fake.base_url)
    with pytest.raises(UpstreamError, match="gibt-es-nicht"):
        await other.list_games()


async def test_login_conflict_reports_server_reason_and_is_retried_later(fake, client):
    from relay.sporteurope_client import LoginError
    populate(fake)
    fake.login_status, fake.login_message = 409, "Maximale Anzahl an Geräten erreicht"
    with pytest.raises(LoginError, match="Maximale Anzahl an Geräten erreicht") as info:
        await client.list_games()
    assert info.value.code == "login_error"
    fake.login_status = None
    client.FAILURE_TTL = 0
    assert len(await client.list_games()) == 4  # not a credential error: next attempt may succeed
