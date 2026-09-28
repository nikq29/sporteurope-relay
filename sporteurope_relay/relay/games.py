"""Team games from Sporteurope listings and whether the account can play them."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Game:
    id: str
    slug: str
    profile_slug: str
    name: str
    home: str
    guest: str
    start: datetime | None
    live: bool
    free: bool
    unlocked: bool | None = None

    def to_json(self) -> dict:
        return {
            "id": self.id, "name": self.name, "home": self.home, "guest": self.guest,
            "start": self.start.isoformat() if self.start else None,
            "live": self.live, "unlocked": self.unlocked,
        }


def _parse_start(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_games(items: list[dict], team_slug: str) -> list[Game]:
    games: dict[str, Game] = {}
    for item in items:
        if item.get("type") != "LIVESTREAM":
            continue
        home = item.get("home_team") or {}
        guest = item.get("guest_team") or {}
        if team_slug not in (home.get("slug"), guest.get("slug")):
            continue
        games[item["id"]] = Game(
            id=item["id"],
            slug=item.get("slug", ""),
            profile_slug=(item.get("profile") or {}).get("slug", ""),
            name=item.get("name") or f"{home.get('name', '')} – {guest.get('name', '')}",
            home=home.get("name", ""),
            guest=guest.get("name", ""),
            start=_parse_start(item.get("content_start_date")),
            live=bool(item.get("currently_live")),
            free=not item.get("monetizations"),
        )
    return sorted(games.values(), key=lambda g: (not g.live, g.start or _FAR_FUTURE))


def collect_ids(obj) -> set[str]:
    found: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                walk(key)
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)
        elif isinstance(value, str) and _UUID.match(value):
            found.add(value.lower())

    walk(obj)
    return found


def is_unlocked(detail: dict, owned: set[str]) -> bool:
    if not detail.get("monetizations") and not detail.get("price_in_cents"):
        return True
    if str(detail.get("id", "")).lower() in owned:
        return True
    return any(str(p.get("id", "")).lower() in owned for p in detail.get("products") or [])
