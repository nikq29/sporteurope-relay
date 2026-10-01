"""In-process fake of api.sporteurope.tv and Mux, shared by client, relay and integration tests."""
import asyncio
from collections import Counter

from aiohttp import web

EMAIL = "fan@example.org"
PASSWORD = "richtig"
TEAM = "ec-kassel-huskies"
OWNED_PRODUCT = "11111111-1111-4111-8111-111111111111"
OTHER_PRODUCT = "22222222-2222-4222-8222-222222222222"
LIVE_ID = "aaaaaaaa-0000-4000-8000-000000000001"
UPCOMING_ID = "aaaaaaaa-0000-4000-8000-000000000002"
LOCKED_ID = "aaaaaaaa-0000-4000-8000-000000000003"
FREE_ID = "aaaaaaaa-0000-4000-8000-000000000004"
OTHER_TEAM_ID = "aaaaaaaa-0000-4000-8000-000000000005"
TEAM_PROFILE_ID = "9bc5fc83-4cc8-467c-b21e-2865759e41a1"


def asset(asset_id, slug, *, live=False, home=TEAM, guest="eispiraten-crimmitschau", kind="LIVESTREAM",
          monetizations=("SUBSCRIPTION", "PAY_PER_VIEW"), start="2026-10-02T17:00:00.000000Z"):
    def team(s):
        return {"id": f"team-{s}", "slug": s, "name": s.replace("-", " ").title()}

    return {
        "id": asset_id, "slug": slug, "type": kind, "currently_live": live, "content_start_date": start,
        "name": f"{team(home)['name']} vs. {team(guest)['name']}",
        "home_team": team(home), "guest_team": team(guest), "monetizations": list(monetizations),
        "price_in_cents": 890 if monetizations else None, "profile": {"slug": "del2"},
    }


class FakeSporteurope:
    def __init__(self):
        self.assets: dict[str, dict] = {}
        self.products: dict[str, list[str]] = {}
        self.owned = [OWNED_PRODUCT]
        self.session_expired = False
        self.stream_status = 200
        self.rendition_status = 200
        self.rendition_body = None
        self.chunk_status: dict[tuple[str, int], int] = {}
        self.ended = False
        self.list_status = 200
        self.detail_status: dict[str, int] = {}
        self.list_delay = 0.0
        self.login_status: int | None = None
        self.login_message = ""
        self.drm_token = None
        self.rendition_key = ""
        self.mux_forbidden = 0
        self.media_seq = 100
        self.live_segments = 4
        self.calls = Counter()
        self.segment_fetches = Counter()
        self._token = 0
        self._session = 0
        app = web.Application()
        app.router.add_get("/api/web/personal/csrf", self._csrf)
        app.router.add_post("/api/web/auth/login", self._login)
        app.router.add_get("/api/web/public/profile-slugs/{slug}", self._profile_slug)
        app.router.add_get("/api/web/public/profiles/{profile_id}/next-livestreams", self._list)
        app.router.add_get("/api/web/public/assets/{profile}/{slug}", self._detail)
        app.router.add_get("/api/web-player/personal/assets/{id}", self._stream_info)
        app.router.add_get("/mux/{id}.m3u8", self._master)
        app.router.add_get("/mux/{height}/rendition.m3u8", self._rendition)
        app.router.add_get("/mux/chunk/{height}/{seq}.ts", self._chunk)
        self.app = app

    def add_asset(self, item, products=()):
        self.assets[item["id"]] = item
        self.products[item["id"]] = list(products)

    def advance(self, n=1):
        """n new segments appear at the live edge; the window slides."""
        self.media_seq += n

    async def _csrf(self, request):
        self.calls["csrf"] += 1
        resp = web.Response(status=204)
        resp.set_cookie("XSRF-TOKEN", "tok%3D")
        return resp

    async def _login(self, request):
        self.calls["login"] += 1
        if request.headers.get("x-xsrf-token") != "tok=":
            return web.json_response({"message": "CSRF token mismatch."}, status=419)
        if self.login_status:
            return web.json_response({"message": self.login_message}, status=self.login_status)
        if await request.json() != {"email": EMAIL, "password": PASSWORD}:
            return web.json_response({"message": "invalid"}, status=422)
        self._session += 1
        self.session_expired = False
        resp = web.json_response(
            {"id": "user", "bought_products_and_asset_ids": {"products": list(self.owned), "assets": []}}, status=201
        )
        resp.set_cookie("session", f"s{self._session}")
        return resp

    async def _profile_slug(self, request):
        self.calls["profile_slug"] += 1
        if request.match_info["slug"] != TEAM:
            return web.json_response({"message": "Not found"}, status=404)
        return web.json_response({"profile_id": TEAM_PROFILE_ID})

    async def _list(self, request):
        self.calls["list"] += 1
        if request.match_info["profile_id"] != TEAM_PROFILE_ID:
            return web.json_response({"message": "Not found"}, status=404)
        if self.list_delay:
            await asyncio.sleep(self.list_delay)
        if self.list_status != 200:
            return web.json_response({}, status=self.list_status)
        page = int(request.query.get("page", "1"))
        per_page = int(request.query.get("per_page", "20"))
        items = list(self.assets.values())
        last = max(1, -(-len(items) // per_page))
        return web.json_response({
            "data": items[(page - 1) * per_page: page * per_page],
            "meta": {"current_page": page, "last_page": last, "per_page": per_page, "total": len(items)},
        })

    async def _detail(self, request):
        self.calls["detail"] += 1
        item = next((a for a in self.assets.values() if a["slug"] == request.match_info["slug"]), None)
        if item is None:
            raise web.HTTPNotFound()
        if item["id"] in self.detail_status:
            return web.json_response({}, status=self.detail_status[item["id"]])
        products = [{"id": p, "name": "Pass", "price_in_cents": 3490, "disabled": False}
                    for p in self.products.get(item["id"], [])]
        return web.json_response({**item, "products": products})

    async def _stream_info(self, request):
        self.calls["stream_info"] += 1
        if "session" not in request.cookies or self.session_expired:
            return web.json_response({"message": "Unauthenticated."}, status=401)
        if request.headers.get("x-version") != "2":
            return web.json_response({"message": "missing x-version"}, status=400)
        if self.stream_status != 200:
            return web.json_response({}, status=self.stream_status)
        self._token += 1
        asset_id = request.match_info["id"]
        base = f"{request.url.scheme}://{request.host}"
        source = {
            "hls": f"{base}/mux/{asset_id}.m3u8?token=t{self._token}",
            "mux": {"playbackId": asset_id,
                    "tokens": {"playback": f"t{self._token}", "storyboard": "s", "thumbnail": "t",
                               "drm": self.drm_token}},
        }
        return web.json_response({"id": asset_id, "tracks": [{"is_primary": True, "sources": [source]}],
                                  "analytics": {"used_monetization": "subscription"}})

    async def _master(self, request):
        self.calls["master"] += 1
        sig = f"t{self._token}"
        return web.Response(text="\n".join([
            "#EXTM3U", "#EXT-X-VERSION:5", "#EXT-X-INDEPENDENT-SEGMENTS",
            '#EXT-X-STREAM-INF:BANDWIDTH=2340800,CODECS="mp4a.40.2,avc1.640020",RESOLUTION=960x540',
            f"/mux/540/rendition.m3u8?signature={sig}",
            '#EXT-X-STREAM-INF:BANDWIDTH=6000000,CODECS="mp4a.40.2,avc1.640020",RESOLUTION=1920x1080',
            f"/mux/1080/rendition.m3u8?signature={sig}", "",
        ]))

    async def _rendition(self, request):
        self.calls["rendition"] += 1
        if self.mux_forbidden > 0:
            self.mux_forbidden -= 1
            return web.Response(status=403)
        if self.rendition_status != 200:
            return web.Response(status=self.rendition_status)
        if self.rendition_body is not None:
            return web.Response(text=self.rendition_body)
        height = request.match_info["height"]
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:1", f"#EXT-X-MEDIA-SEQUENCE:{self.media_seq}"]
        if self.rendition_key:
            lines.append(self.rendition_key)
        for seq in range(self.media_seq, self.media_seq + self.live_segments):
            lines += ["#EXTINF:1.000,", f"/mux/chunk/{height}/{seq}.ts?signature=t{self._token}"]
        if self.ended:
            lines.append("#EXT-X-ENDLIST")
        return web.Response(text="\n".join(lines) + "\n")

    async def _chunk(self, request):
        key = (request.match_info["height"], int(request.match_info["seq"]))
        self.segment_fetches[key] += 1
        if key in self.chunk_status:
            return web.Response(status=self.chunk_status[key])
        return web.Response(body=f"seg-{key[0]}-{key[1]}".encode(), content_type="video/mp2t")
