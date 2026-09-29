"""Talks to api.sporteurope.tv the way the web player does: one login, same headers, same cadence."""
import asyncio
import logging
import time
from dataclasses import dataclass, replace
from urllib.parse import unquote

import aiohttp

from relay.games import Game, collect_ids, is_unlocked, parse_games

API_BASE = "https://api.sporteurope.tv"
WEB_ORIGIN = "https://sporteurope.tv"

log = logging.getLogger(__name__)


class SporteuropeError(Exception):
    code = "upstream"


class UpstreamError(SporteuropeError):
    code = "upstream"


class HttpStatusError(UpstreamError):
    def __init__(self, status: int, path: str):
        super().__init__(f"HTTP {status} für {path}")
        self.status = status


class LoginFailed(SporteuropeError):
    code = "login_failed"


class NotPurchased(SporteuropeError):
    code = "not_purchased"


class DrmProtected(SporteuropeError):
    code = "drm"


class StreamInUse(SporteuropeError):
    code = "stream_in_use"


@dataclass(frozen=True)
class StreamInfo:
    master_url: str
    drm: bool


class SporteuropeClient:
    GAMES_TTL = 60.0
    FAILURE_TTL = 15.0
    PER_PAGE = 50
    MAX_PAGES = 10

    def __init__(self, http: aiohttp.ClientSession, email: str, password: str, team_slug: str, *,
                 base_url: str = API_BASE):
        self._http = http
        self._email = email
        self._password = password
        self._team_slug = team_slug
        self._base = base_url.rstrip("/")
        self._logged_in = False
        self._login_error: LoginFailed | None = None
        self._login_lock = asyncio.Lock()
        self._games: tuple[float, list[Game]] | None = None
        self._games_lock = asyncio.Lock()
        self._games_failure: tuple[float, UpstreamError] | None = None
        self._unlocked: dict[str, bool] = {}
        self._team_profile_id: str | None = None
        self.owned_ids: set[str] = set()
        self.login_body: dict = {}

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "Origin": WEB_ORIGIN, "Referer": WEB_ORIGIN + "/"}
        if extra:
            headers.update(extra)
        return headers

    def _xsrf_token(self) -> str:
        for cookie in self._http.cookie_jar:
            if cookie.key == "XSRF-TOKEN":
                return unquote(cookie.value)
        return ""

    async def _request(self, method: str, path: str, *, params=None, json=None, headers=None) -> dict:
        try:
            async with self._http.request(method, self._base + path, params=params, json=json,
                                          headers=self._headers(headers)) as resp:
                if resp.status >= 400:
                    raise HttpStatusError(resp.status, path)
                if resp.status == 204:
                    return {}
                body = await resp.json(content_type=None)
                return body if isinstance(body, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise UpstreamError(f"{type(exc).__name__} für {path}") from exc

    async def login(self) -> None:
        if self._login_error:
            raise self._login_error
        async with self._login_lock:
            if self._login_error:
                raise self._login_error
            if self._logged_in:
                return
            await self._request("GET", "/api/web/personal/csrf", params={"lang": "de"})
            try:
                body = await self._request(
                    "POST", "/api/web/auth/login", params={"lang": "de"},
                    json={"email": self._email, "password": self._password},
                    headers={"x-xsrf-token": self._xsrf_token()},
                )
            except HttpStatusError as exc:
                if exc.status in (401, 403, 422):
                    log.error("Sporteurope login rejected (HTTP %s); not retrying until the add-on restarts", exc.status)
                    self._login_error = LoginFailed("Zugangsdaten abgelehnt")
                    raise self._login_error from exc
                raise
            self.login_body = body
            self.owned_ids = collect_ids(body.get("bought_products_and_asset_ids"))
            self._unlocked.clear()
            self._logged_in = True
            log.info("Logged in to Sporteurope (%d owned product/asset ids)", len(self.owned_ids))

    async def _personal_get(self, path: str, headers: dict | None = None) -> dict:
        if not self._logged_in:
            await self.login()
        try:
            return await self._request("GET", path, headers=headers)
        except HttpStatusError as exc:
            if exc.status != 401:
                raise
        log.info("Sporteurope session expired, logging in again")
        self._logged_in = False
        await self.login()
        return await self._request("GET", path, headers=headers)

    async def list_games(self) -> list[Game]:
        async with self._games_lock:  # many TVs, one upstream fetch
            now = time.monotonic()
            if self._games and now - self._games[0] < self.GAMES_TTL:
                return self._games[1]
            if self._games_failure and now - self._games_failure[0] < self.FAILURE_TTL:
                raise self._games_failure[1]
            try:
                games = await self._fetch_games()
            except UpstreamError as exc:
                if self._games:
                    log.warning("Game list refresh failed (%s), serving the last good list", exc)
                    self._games = (now, self._games[1])
                    return self._games[1]
                self._games_failure = (now, exc)
                raise
            self._games, self._games_failure = (now, games), None
            return games

    async def _fetch_games(self) -> list[Game]:
        if not self._logged_in:
            await self.login()
        profile_id = await self._resolve_team_profile()
        items: list[dict] = []
        for page in range(1, self.MAX_PAGES + 1):
            body = await self._request("GET", f"/api/web/public/profiles/{profile_id}/next-livestreams",
                                       params={"page": page, "per_page": self.PER_PAGE, "lang": "de"})
            items += body.get("data") or []
            if page >= int((body.get("meta") or {}).get("last_page", page)):
                break
        return [await self._with_unlock(game) for game in parse_games(items, self._team_slug)]

    async def _resolve_team_profile(self) -> str:
        """The team page resolves its slug the same way; the id never changes, so ask once."""
        if self._team_profile_id is None:
            try:
                body = await self._request("GET", f"/api/web/public/profile-slugs/{self._team_slug}", params={"lang": "de"})
            except HttpStatusError as exc:
                if exc.status == 404:
                    raise UpstreamError(f"Team '{self._team_slug}' gibt es bei Sporteurope nicht") from exc
                raise
            if not body.get("profile_id"):
                raise UpstreamError(f"Team '{self._team_slug}' ohne profile_id")
            self._team_profile_id = body["profile_id"]
            log.info("Team %s has profile id %s", self._team_slug, self._team_profile_id)
        return self._team_profile_id

    async def _with_unlock(self, game: Game) -> Game:
        if game.id not in self._unlocked:
            if game.free or game.id.lower() in self.owned_ids:
                self._unlocked[game.id] = True
            else:
                try:
                    detail = await self._request("GET", f"/api/web/public/assets/{game.profile_slug}/{game.slug}",
                                                 params={"lang": "de"})
                except UpstreamError as exc:
                    log.warning("Unlock status unknown for %s (%s)", game.name, exc)
                    return replace(game, unlocked=None)
                self._unlocked[game.id] = is_unlocked(detail, self.owned_ids)
        return replace(game, unlocked=self._unlocked[game.id])

    async def stream_info(self, asset_id: str) -> StreamInfo:
        try:
            body = await self._personal_get(f"/api/web-player/personal/assets/{asset_id}",
                                            headers={"x-version": "2", "x-accept-language": "de"})
        except HttpStatusError as exc:
            if exc.status in (402, 403):
                raise NotPurchased(f"HTTP {exc.status}") from exc
            if exc.status in (401, 409, 423, 429):
                raise StreamInUse(f"HTTP {exc.status}") from exc
            raise
        top_drm = ((body.get("mux") or {}).get("tokens") or {}).get("drm")
        tracks = sorted(body.get("tracks") or [], key=lambda t: not t.get("is_primary"))
        for track in tracks:
            for source in track.get("sources") or []:
                if source.get("hls"):
                    drm = ((source.get("mux") or {}).get("tokens") or {}).get("drm")
                    return StreamInfo(source["hls"], drm=drm is not None or top_drm is not None)
        raise NotPurchased("keine abspielbare Quelle")
