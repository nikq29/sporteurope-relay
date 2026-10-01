"""One upstream HLS session shared by every TV on the LAN."""
import asyncio
import logging
import time
from contextlib import suppress

import aiohttp

from relay import playlist
from relay.games import Game
from relay.logsafe import redact_url
from relay.segment_buffer import SegmentBuffer
from relay.sporteurope_client import (WEB_ORIGIN, DrmProtected, NotPurchased, SporteuropeClient, SporteuropeError,
                                      StreamInUse, UpstreamError)

log = logging.getLogger(__name__)


SEGMENT_ATTEMPTS = 3  # a segment Mux keeps failing to deliver is skipped instead of stopping the relay


class MuxForbidden(Exception):
    """Mux answered 401/403/410: the signed URL expired or access was revoked."""


class MuxHttpError(UpstreamError):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} für {redact_url(url)}")
        self.status = status


class HlsRelay:
    def __init__(self, client: SporteuropeClient, http: aiohttp.ClientSession, *, max_height: int = 1080,
                 idle_timeout: float = 120.0, refresh_interval: float = 60.0, poll_interval: float | None = None,
                 window: int = 10, initial_segments: int = 3, buffer_seconds: float = 120.0,
                 max_outage: float = 30.0, viewer_ttl: float = 30.0):
        self._client = client
        self._http = http
        self._max_height = max_height
        self._idle_timeout = idle_timeout
        self._refresh_interval = refresh_interval
        self._poll_interval = poll_interval
        self._window = window
        self._initial_segments = initial_segments
        self._max_outage = max_outage
        self._viewer_ttl = viewer_ttl
        self._buffer = SegmentBuffer(buffer_seconds)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.state = "idle"
        self.error: str | None = None
        self.game: Game | None = None
        self._viewers: dict[str, float] = {}
        self._reset(None)

    def _reset(self, game: Game | None) -> None:
        self.game = game
        self._buffer.reset()
        self._upstream_last: int | None = None
        self._segment_failures: dict[int, int] = {}
        self._rendition_url: str | None = None
        self._target = 6
        self._ended = False
        self._last_refresh = 0.0
        self._last_access = time.monotonic()

    # --- control -------------------------------------------------------

    async def start(self, game: Game) -> None:
        async with self._lock:
            await self._cancel_task()
            self._reset(game)
            self.state, self.error = "starting", None
            try:
                await self._refresh()
                await self._poll()
            except SporteuropeError as exc:
                self._halt(exc.code)
                raise
            except Exception as exc:
                log.exception("Unexpected error while starting the relay")
                self._halt(UpstreamError.code)
                raise UpstreamError(f"{type(exc).__name__} beim Start") from exc
            self.state = "live"
            log.info("Relay live: %s", game.name)
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        async with self._lock:
            await self._cancel_task()
            self._halt(None)

    async def _cancel_task(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _halt(self, error: str | None) -> None:
        if error:
            log.warning("Relay stopped: %s", error)
        elif self.game:
            log.info("Relay stopped, upstream released")
        self.state = "error" if error else "idle"
        self.error = error
        if error is None:
            self.game = None
        self._buffer.reset()

    # --- viewers -------------------------------------------------------

    def touch(self, client_ip: str) -> None:
        now = time.monotonic()
        self._last_access = now
        self._viewers[client_ip] = now

    def viewers(self) -> int:
        now = time.monotonic()
        self._viewers = {ip: t for ip, t in self._viewers.items() if now - t <= self._viewer_ttl}
        return len(self._viewers)

    def playlist_text(self) -> str | None:
        if self.state not in ("live", "ended") or len(self._buffer) == 0:
            return None
        return playlist.render_media(self._buffer.window(self._window), self._target, ended=self._ended)

    def segment(self, seq: int) -> bytes | None:
        return self._buffer.get(seq)

    def status(self) -> dict:
        return {"state": self.state, "game": self.game.to_json() if self.game else None,
                "viewers": self.viewers(), "error": self.error}

    # --- upstream ------------------------------------------------------

    async def _run(self) -> None:
        outage_since: float | None = None
        backoff = 1.0
        try:
            while True:
                await asyncio.sleep(self._poll_interval or max(1.0, self._target / 2))
                now = time.monotonic()
                if now - self._last_access > self._idle_timeout:
                    log.info("No viewer for %.0f s", self._idle_timeout)
                    self._halt(None)
                    return
                if self.state == "ended":
                    continue  # nothing left upstream; keep serving the buffer until nobody watches
                if now - self._last_refresh >= self._refresh_interval:
                    try:
                        await self._refresh()
                    except UpstreamError as exc:
                        # The current signed URL usually still works: keep feeding the TVs, retry soon.
                        log.warning("Stream info refresh failed (%s), keeping the current URL", exc)
                        self._last_refresh = now - self._refresh_interval + min(10.0, self._refresh_interval)
                try:
                    await self._poll()
                except UpstreamError as exc:
                    if outage_since is None:
                        outage_since = now
                    if now - outage_since > self._max_outage:
                        self._halt(UpstreamError.code)
                        return
                    log.warning("Upstream error (%s), retrying in %.0f s", exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 8.0)
                    continue
                outage_since, backoff = None, 1.0
                if self._ended:
                    log.info("Upstream playlist ended: game over")
                    self.state = "ended"
        except NotPurchased:
            # We were allowed to play a moment ago, so the slot was taken elsewhere.
            self._halt(StreamInUse.code)
        except SporteuropeError as exc:
            self._halt(exc.code)
        except Exception:
            log.exception("Unexpected error in the relay loop")
            self._halt(UpstreamError.code)

    async def _refresh(self) -> None:
        info = await self._client.stream_info(self.game.id)
        if info.drm:
            raise DrmProtected("tokens.drm gesetzt")
        try:
            master = await self._fetch_text(info.master_url)
        except MuxForbidden as exc:
            raise StreamInUse("Mux verweigert die Master-Playlist") from exc
        if playlist.has_drm(master):
            raise DrmProtected("Schlüssel in der Master-Playlist")
        try:
            variants = playlist.parse_master(master, info.master_url)
        except ValueError as exc:
            raise UpstreamError("unlesbare Master-Playlist") from exc
        if not variants:
            raise UpstreamError("Master-Playlist ohne Varianten")
        self._rendition_url = playlist.pick_variant(variants, self._max_height).uri
        self._last_refresh = time.monotonic()

    async def _poll(self) -> None:
        try:
            await self._poll_once()
        except MuxForbidden:
            log.info("Signed Mux URL rejected, refreshing stream info")
            await self._refresh()
            try:
                await self._poll_once()
            except MuxForbidden as exc:
                raise StreamInUse("Mux verweigert den Zugriff") from exc

    async def _poll_once(self) -> None:
        text = await self._fetch_text(self._rendition_url)
        if playlist.has_drm(text):
            raise DrmProtected("Schlüssel in der Rendition-Playlist")
        try:
            media = playlist.parse_media(text, self._rendition_url)
        except ValueError as exc:
            raise UpstreamError("unlesbare Rendition-Playlist") from exc
        self._target = max(1, media.target_duration)
        self._ended = media.ended
        if not media.segments:
            return
        if self._upstream_last is not None and media.segments[-1].seq < self._upstream_last:
            log.warning("Upstream media sequence went backwards, continuing after a discontinuity")
            self._buffer.mark_discontinuity()
            self._upstream_last = None
        last = self._upstream_last
        new = [s for s in media.segments if last is None or s.seq > last]
        if last is None:
            new = new[-self._initial_segments:]
        for seg in new:
            try:
                data = await self._fetch(seg.uri)
            except MuxHttpError as exc:
                attempts = self._segment_failures.get(seg.seq, 0) + 1
                if exc.status != 404 and attempts < SEGMENT_ATTEMPTS:
                    self._segment_failures[seg.seq] = attempts
                    raise
                log.warning("Skipping upstream segment %d (HTTP %s)", seg.seq, exc.status)
                self._segment_failures.pop(seg.seq, None)
                self._buffer.mark_discontinuity()
                self._upstream_last = seg.seq
                continue
            self._segment_failures.pop(seg.seq, None)
            self._buffer.append(seg.duration, data, discontinuity=seg.discontinuity)
            self._upstream_last = seg.seq

    async def _fetch(self, url: str) -> bytes:
        try:
            async with self._http.get(url, headers={"Origin": WEB_ORIGIN, "Referer": WEB_ORIGIN + "/"}) as resp:
                if resp.status in (401, 403, 410):
                    raise MuxForbidden(resp.status)
                if resp.status >= 400:
                    raise MuxHttpError(resp.status, url)
                return await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise UpstreamError(f"{type(exc).__name__} für {redact_url(url)}") from exc

    async def _fetch_text(self, url: str) -> str:
        return (await self._fetch(url)).decode("utf-8", "replace")
