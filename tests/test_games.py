from datetime import datetime, timezone

from relay.games import collect_ids, is_unlocked, parse_games

TEAM = "ec-kassel-huskies"


def item(id_, *, home=TEAM, guest="eispiraten-crimmitschau", kind="LIVESTREAM", live=False,
         start="2026-10-02T17:00:00.000000Z", monetizations=("SUBSCRIPTION",)):
    return {
        "id": id_, "slug": f"slug-{id_}", "type": kind, "name": f"Spiel {id_}", "currently_live": live,
        "content_start_date": start, "monetizations": list(monetizations), "profile": {"slug": "del2"},
        "home_team": {"slug": home, "name": home.title()} if home else None,
        "guest_team": {"slug": guest, "name": guest.title()},
    }


def test_parse_games_keeps_only_team_livestreams():
    games = parse_games(
        [item("a"), item("b", home="other", guest=TEAM), item("c", home="x", guest="y"),
         item("d", kind="VIDEO"), item("e", home=None, guest="y")],
        TEAM,
    )
    assert [g.id for g in games] == ["a", "b"]


def test_parse_games_sorts_live_first_then_by_start_and_dedupes():
    games = parse_games(
        [item("late", start="2026-10-09T17:00:00Z"), item("soon"), item("live", live=True, start="2026-09-28T17:00:00Z"),
         item("soon")],
        TEAM,
    )
    assert [g.id for g in games] == ["live", "soon", "late"]


def test_parse_games_maps_fields():
    game = parse_games([item("a", monetizations=())], TEAM)[0]
    assert game.start == datetime(2026, 10, 2, 17, 0, tzinfo=timezone.utc)
    assert (game.profile_slug, game.slug, game.free, game.unlocked) == ("del2", "slug-a", True, None)
    assert game.to_json() == {
        "id": "a", "name": "Spiel a", "home": "Ec-Kassel-Huskies", "guest": "Eispiraten-Crimmitschau",
        "start": "2026-10-02T17:00:00+00:00", "live": False, "unlocked": None,
    }


def test_parse_games_tolerates_missing_start():
    assert parse_games([item("a", start=None)], TEAM)[0].start is None


def test_collect_ids_finds_uuids_in_any_shape():
    owned = collect_ids({
        "products": ["11111111-1111-4111-8111-111111111111"],
        "assets": {"AAAAAAAA-0000-4000-8000-000000000001": True},
        "nested": [{"id": "22222222-2222-4222-8222-222222222222", "name": "not an id"}],
    })
    assert owned == {
        "11111111-1111-4111-8111-111111111111",
        "aaaaaaaa-0000-4000-8000-000000000001",
        "22222222-2222-4222-8222-222222222222",
    }


def test_is_unlocked_rules():
    owned = {"11111111-1111-4111-8111-111111111111", "aaaaaaaa-0000-4000-8000-000000000009"}
    product = lambda pid: {"id": pid, "name": "Pass"}
    assert is_unlocked({"id": "x", "monetizations": [], "price_in_cents": None}, set()) is True
    assert is_unlocked({"id": "AAAAAAAA-0000-4000-8000-000000000009", "monetizations": ["PAY_PER_VIEW"]}, owned) is True
    assert is_unlocked({"id": "x", "monetizations": ["SUBSCRIPTION"],
                        "products": [product("99999999-9999-4999-8999-999999999999"),
                                     product("11111111-1111-4111-8111-111111111111")]}, owned) is True
    assert is_unlocked({"id": "x", "monetizations": ["SUBSCRIPTION"],
                        "products": [product("99999999-9999-4999-8999-999999999999")]}, owned) is False
