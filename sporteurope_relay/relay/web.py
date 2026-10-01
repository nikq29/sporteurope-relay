"""HTTP routes for the TVs: page, JSON API, relay playlist and segments."""
import base64
import binascii
import hashlib
import hmac
import ipaddress
import logging
import os
import time
from pathlib import Path

from aiohttp import web

from relay.sporteurope_client import LoginFailed, SporteuropeError

STATIC_DIR = Path(__file__).parent / "static"

MESSAGES = {
    "login_failed": "Login fehlgeschlagen – Zugangsdaten in HA prüfen",
    "not_purchased": "🔒 Nicht gekauft",
    "drm": "Dieses Spiel ist DRM-geschützt – nicht unterstützt",
    "stream_in_use": "Stream wird an anderer Stelle genutzt",
    "upstream": "Verbindung zu Sporteurope unterbrochen",
    "not_live": "Spiel ist noch nicht live",
    "unknown_game": "Spiel nicht gefunden",
    "ended": "Spiel beendet",
}
_HTTP_STATUS = {"login_failed": 401, "not_purchased": 403, "drm": 403, "stream_in_use": 409, "not_live": 409,
                "unknown_game": 404, "upstream": 502}
_NO_CACHE = {"Cache-Control": "no-cache"}

MAX_AUTH_FAILURES = 10
AUTH_LOCKOUT_SECONDS = 600.0
_REALM = 'Basic realm="Sporteurope Relay", charset="UTF-8"'
STREAM_TOKEN_TTL = 6 * 3600
_CORS = {"Access-Control-Allow-Origin": "*"}  # Chromecast's receiver page loads the stream cross-origin

log = logging.getLogger(__name__)


def _from_tunnel(request: web.Request) -> bool:
    """Cloudflared always adds Cf-* headers; anything else from a non-private address is external too."""
    if "Cf-Connecting-Ip" in request.headers or "Cf-Ray" in request.headers:
        return True
    try:
        address = ipaddress.ip_address(request.remote or "")
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback)


def _basic_password(request: web.Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return decoded.split(":", 1)[1] if ":" in decoded else None


class StreamTokens:
    """Signed, expiring stream links for AirPlay/Chromecast receivers, which cannot send the password.

    The secret lives only in this process, so a restart invalidates every link.
    """

    def __init__(self, ttl: int = STREAM_TOKEN_TTL):
        self._secret = os.urandom(32)
        self._ttl = ttl

    def _sign(self, expires: str) -> str:
        return hmac.new(self._secret, expires.encode(), hashlib.sha256).hexdigest()[:32]

    def issue(self) -> str:
        expires = str(int(time.time()) + self._ttl)
        return f"{expires}.{self._sign(expires)}"

    def valid(self, token: str) -> bool:
        expires, _, signature = token.partition(".")
        if not expires.isdigit() or not hmac.compare_digest(signature, self._sign(expires)):
            return False
        return time.time() < int(expires)


def _is_stream_path(path: str) -> bool:
    return path == "/live.m3u8" or path.startswith("/seg/")


def _remote_auth_middleware(remote_password: str, tokens: StreamTokens):
    failures: dict[str, list[float]] = {}

    @web.middleware
    async def middleware(request, handler):
        if not _from_tunnel(request):
            return await handler(request)
        if not remote_password:
            return web.Response(status=403, text="Zugriff von außen ist nicht freigegeben\n")
        client = request.headers.get("Cf-Connecting-Ip") or request.remote or "?"
        now = time.monotonic()
        recent = [t for t in failures.get(client, []) if now - t < AUTH_LOCKOUT_SECONDS]
        if len(recent) >= MAX_AUTH_FAILURES:
            failures[client] = recent
            return web.Response(status=429, text="Zu viele Fehlversuche, später erneut probieren\n")
        token = request.query.get("t")
        if token is not None and _is_stream_path(request.path):
            if tokens.valid(token):
                return await handler(request)
            recent.append(now)
            failures[client] = recent
            log.warning("Invalid or expired stream link from %s", client)
            return web.Response(status=401, text="Link ungültig oder abgelaufen\n")
        given = _basic_password(request)
        if given is not None and hmac.compare_digest(given.encode(), remote_password.encode()):
            failures.pop(client, None)
            return await handler(request)
        if given is not None:
            recent.append(now)
            log.warning("Wrong remote password from %s (%d/%d)", client, len(recent), MAX_AUTH_FAILURES)
        failures[client] = recent
        return web.Response(status=401, text="Passwort erforderlich\n", headers={"WWW-Authenticate": _REALM})

    return middleware


def status_message(status: dict) -> str | None:
    if status["error"]:
        return MESSAGES.get(status["error"])
    return MESSAGES["ended"] if status["state"] == "ended" else None


def _error(code: str) -> web.Response:
    return web.json_response({"error": code, "message": MESSAGES[code]}, status=_HTTP_STATUS[code])


def create_app(client, relay, static_dir: Path = STATIC_DIR, *, remote_password: str = "",
               stream_token_ttl: int = STREAM_TOKEN_TTL) -> web.Application:
    tokens = StreamTokens(stream_token_ttl)

    async def index(request):
        return web.FileResponse(static_dir / "index.html", headers=_NO_CACHE)

    async def games(request):
        try:
            items = await client.list_games()
        except LoginFailed:
            return web.json_response({"games": [], "error": "login_failed", "message": MESSAGES["login_failed"]})
        except SporteuropeError as exc:
            return _error(exc.code)
        return web.json_response({"games": [g.to_json() for g in items], "error": None, "message": None})

    async def play(request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        game_id = str(body.get("game_id", ""))
        try:
            items = await client.list_games()
        except SporteuropeError as exc:
            return _error(exc.code)
        game = next((g for g in items if g.id == game_id), None)
        if game is None:
            return _error("unknown_game")
        # A cached "not purchased" may be stale (bought on the website since); stream info decides.
        if not game.live:
            return _error("not_live")
        if relay.game is not None and relay.game.id == game.id and relay.state in ("starting", "live"):
            return web.json_response(relay.status())
        try:
            await relay.start(game)
        except SporteuropeError as exc:
            return _error(exc.code)
        return web.json_response(relay.status())

    async def stop(request):
        await relay.stop()
        return web.json_response(relay.status())

    async def status(request):
        data = relay.status()
        data["message"] = status_message(data)
        return web.json_response(data, headers=_NO_CACHE)

    async def stream_url(request):
        """Link for receivers (AirPlay, Chromecast, VLC): the public URL the viewer used, plus a token."""
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme) if _from_tunnel(request) else request.scheme
        url = f"{scheme}://{request.host}/live.m3u8?t={tokens.issue()}"
        return web.json_response({"url": url, "expires_in": stream_token_ttl}, headers=_NO_CACHE)

    async def live(request):
        relay.touch(request.remote or "?")
        text = relay.playlist_text()
        if text is None:
            return web.Response(status=503, text="Kein aktives Spiel\n", headers={"Retry-After": "2", **_CORS})
        token = request.query.get("t")
        if token:  # receivers resolve seg/ relative to the playlist and drop its query: carry the token along
            text = "\n".join(f"{line}?t={token}" if line.startswith("seg/") else line for line in text.split("\n"))
        return web.Response(text=text, content_type="application/vnd.apple.mpegurl", headers={**_NO_CACHE, **_CORS})

    async def segment(request):
        relay.touch(request.remote or "?")
        data = relay.segment(int(request.match_info["seq"]))
        if data is None:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type="video/mp2t", headers={"Cache-Control": "max-age=300", **_CORS})

    app = web.Application(middlewares=[_remote_auth_middleware(remote_password, tokens)])
    app.router.add_get("/", index)
    app.router.add_get("/api/games", games)
    app.router.add_post("/api/play", play)
    app.router.add_post("/api/stop", stop)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/stream-url", stream_url)
    app.router.add_get("/live.m3u8", live)
    app.router.add_get(r"/seg/{seq:\d+}.ts", segment)
    app.router.add_static("/static/", static_dir)
    return app
