# Sporteurope Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Home Assistant add-on that pulls one Sporteurope/Mux HLS stream of a live EC Kassel Huskies game and serves it to every TV on the LAN at `http://192.168.0.90:8099/`.

**Architecture:** One asyncio/aiohttp process. `sporteurope_client` logs in and talks to `api.sporteurope.tv` the same way the web player does. `hls_relay` keeps a single upstream session: it picks one rendition, downloads each new segment exactly once into an in-memory buffer, and renumbers segments locally. `web` serves a rewritten playlist, the segments, a small JSON API and a TV-friendly page with a locally hosted hls.js. `ha_status` mirrors the relay state into `sensor.sporteurope_relay`. The pure logic (playlist parsing, buffer, team filter, unlock rules) lives in its own modules and is unit-tested. The network parts are tested against an in-process fake of Sporteurope and Mux.

**Tech Stack:** Python 3.13 (image) / ≥3.12 (local), aiohttp, pytest + pytest-aiohttp, hls.js 1.5.20 (vendored), Home Assistant add-on installed from a public GitHub repository.

**Spec:** `docs/superpowers/specs/2026-09-28-sporteurope-relay-design.md`

**Facts from the HAR capture (2026-09-28) that this plan relies on:**
- Stream info returns `tracks[].sources[].hls` and `tracks[].sources[].mux.tokens.{playback, storyboard, thumbnail, drm}`. `mux` sits **inside each source**, not at the top level as the spec's table implies. The client checks both places.
- The web player sends `x-version: 2` and `x-accept-language: de` on stream info.
- Asset listings contain `id, slug, type, name, currently_live, content_start_date, home_team{slug,name}, guest_team{slug,name}, monetizations[], price_in_cents, profile{slug}`. Only the asset detail endpoint has `products[{id, name, price_in_cents, disabled}]`.
- The master playlist has absolute rendition URLs on `manifest-*.mux.com` with `RESOLUTION=WxH`. Rendition playlists have absolute, individually signed segment URLs on `chunk-*.mux.com`.
- The login response shape (`bought_products_and_asset_ids`) is **not** in the capture. Task 6 checks it against the real account.

## Global Constraints

- LAN only: listen on port **8099**; never add this port to the Cloudflared add-on.
- One upstream session at a time; only one game can be active; switching games switches every TV.
- Refuse DRM: `tokens.drm` non-null, or an `#EXT-X-KEY` / `#EXT-X-SESSION-KEY` with `METHOD` other than `NONE` or with `KEYFORMAT`, in the master or rendition playlist.
- Look like one normal viewer: headers `Origin: https://sporteurope.tv`, `Referer: https://sporteurope.tv/`; stream info with `x-version: 2`, `x-accept-language: de`; refresh stream info every **60 s**; game list cached **60 s** no matter how many TVs ask.
- Never log the password, tokens or signed URLs; strip query strings from every URL in log output.
- Add-on options: `email`, `password`, `team_slug` (default `ec-kassel-huskies`), `max_height` (default `1080`), read from `/data/options.json`.
- Rendition: highest height ≤ `max_height`; buffer ~**120 s**; stop upstream after **120 s** without any playlist/segment request.
- Only `type == "LIVESTREAM"` assets where `home_team.slug` or `guest_team.slug` equals `team_slug`.
- User-facing German texts, verbatim: `Login fehlgeschlagen – Zugangsdaten in HA prüfen`, `🔒 Nicht gekauft`, `Dieses Spiel ist DRM-geschützt – nicht unterstützt`, `Stream wird an anderer Stelle genutzt`.
- No CDN at runtime: hls.js is served by the add-on.
- No test touches the real account; only the manual Task 6 and Task 13 do.

## Review Focus

1. **A second TV selects the game that is already playing.** It must join the running relay, not restart the upstream session (test in Task 8).
2. **The Mux live playlist carries a long DVR window (20+ segments).** On start the relay fetches only the newest 3, not the whole window (test in Task 7).
3. **The upstream media sequence goes backwards (encoder restart).** The relay keeps its local numbering increasing, marks a discontinuity and keeps playing (test in Task 7).
4. **Access is revoked mid-game (stream info 403 on refresh, or Mux 403 twice in a row).** The relay stops with "Stream wird an anderer Stelle genutzt" and stops polling instead of fighting for the slot (tests in Task 7).
5. **Many TVs poll `/api/games`.** At most one upstream list call per 60 s, and asset details are fetched once per game (test in Task 5).

---

## File Structure

```
repository.yaml                       HA add-on repository manifest
pyproject.toml                        pytest config (pythonpath, asyncio mode)
requirements-dev.txt                  test deps
sporteurope_relay/                    the add-on folder copied to /addons
  config.yaml                         add-on manifest (options, port, HA API)
  Dockerfile
  requirements.txt                    runtime deps (aiohttp)
  DOCS.md                             user docs shown in HA
  relay/
    __init__.py
    __main__.py                       `python -m relay`
    app.py                            build_app() wiring + main()
    config.py                         Config + load_config()
    logsafe.py                        URL redaction + logging setup
    playlist.py                       parse master/media, pick variant, DRM check, render playlist
    segment_buffer.py                 in-memory segments with local numbering
    games.py                          Game model, team filter, unlock rules
    sporteurope_client.py             login/session, list games, stream info, error types
    hls_relay.py                      the single upstream session
    web.py                            HTTP routes
    ha_status.py                      sensor.sporteurope_relay publisher
    static/index.html, app.js, style.css, hls.min.js
tests/
  conftest.py                         fixtures: fake server, http session, client
  fakes.py                            fake Sporteurope API + fake Mux
  stubs.py                            StubClient / StubRelay / make_game for route tests
  test_config.py, test_logsafe.py, test_playlist.py, test_segment_buffer.py,
  test_games.py, test_sporteurope_client.py, test_hls_relay.py, test_web.py,
  test_static.py, test_ha_status.py, test_integration.py
tools/smoke.py                        manual check against the real account
```

---

### Task 1: Project scaffold, config and safe logging

**Files:**
- Create: `pyproject.toml`, `requirements-dev.txt`, `sporteurope_relay/requirements.txt`, `sporteurope_relay/relay/__init__.py`, `sporteurope_relay/relay/config.py`, `sporteurope_relay/relay/logsafe.py`
- Test: `tests/test_config.py`, `tests/test_logsafe.py`

**Interfaces:**
- Produces: `Config(email: str, password: str, team_slug: str = "ec-kassel-huskies", max_height: int = 1080)` (frozen, password hidden from repr); `load_config(path: str = OPTIONS_PATH) -> Config`; `OPTIONS_PATH = "/data/options.json"`; `redact_url(url: str) -> str`; `redact_text(text: str) -> str`; `RedactingFormatter(logging.Formatter)`; `setup_logging(level: int = logging.INFO) -> None`.

- [ ] **Step 1: Create the scaffold and virtualenv**

`pyproject.toml`:
```toml
[project]
name = "sporteurope-relay"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
pythonpath = ["sporteurope_relay", "tests"]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

`sporteurope_relay/requirements.txt`:
```
aiohttp>=3.11,<4
```

`requirements-dev.txt`:
```
-r sporteurope_relay/requirements.txt
pytest>=8
pytest-aiohttp>=1.1
```

`sporteurope_relay/relay/__init__.py`: empty file.

Run:
```bash
python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
```
Expected: installs without errors.

- [ ] **Step 2: Write the failing tests**

`tests/test_config.py`:
```python
import json

from relay.config import Config, load_config


def test_load_config_applies_defaults(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"email": "fan@example.org", "password": "pw"}))
    assert load_config(str(path)) == Config(
        email="fan@example.org", password="pw", team_slug="ec-kassel-huskies", max_height=1080
    )


def test_load_config_reads_overrides(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"email": "a@b.de", "password": "pw", "team_slug": "other", "max_height": 720}))
    cfg = load_config(str(path))
    assert (cfg.team_slug, cfg.max_height) == ("other", 720)


def test_config_repr_hides_password():
    assert "geheim" not in repr(Config(email="a@b.de", password="geheim"))
```

`tests/test_logsafe.py`:
```python
import logging
import sys

from relay.logsafe import RedactingFormatter, redact_url


def test_redact_url_strips_query():
    assert redact_url("https://stream.mux.com/abc.m3u8?token=SECRET") == "https://stream.mux.com/abc.m3u8"


def test_formatter_strips_queries_from_messages_and_tracebacks():
    try:
        raise RuntimeError("GET https://x.mux.com/r.m3u8?signature=SECRET2 failed")
    except RuntimeError:
        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "fetch %s", ("https://a.example/b?token=SECRET1",), sys.exc_info()
        )
    out = RedactingFormatter("%(message)s").format(record)
    assert "SECRET1" not in out and "SECRET2" not in out
    assert "https://a.example/b" in out and "https://x.mux.com/r.m3u8" in out
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_logsafe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.config'`.

- [ ] **Step 4: Implement**

`sporteurope_relay/relay/config.py`:
```python
"""Add-on options written by the Supervisor to /data/options.json."""
import json
from dataclasses import dataclass, field

OPTIONS_PATH = "/data/options.json"


@dataclass(frozen=True)
class Config:
    email: str
    password: str = field(repr=False)
    team_slug: str = "ec-kassel-huskies"
    max_height: int = 1080


def load_config(path: str = OPTIONS_PATH) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return Config(
        email=raw["email"],
        password=raw["password"],
        team_slug=raw.get("team_slug") or "ec-kassel-huskies",
        max_height=int(raw.get("max_height") or 1080),
    )
```

`sporteurope_relay/relay/logsafe.py`:
```python
"""Logging that never leaks tokens: every URL loses its query string."""
import logging
import re

_URL_WITH_QUERY = re.compile(r"(https?://[^\s?\"'<>]+)\?[^\s\"'<>]*")


def redact_url(url: str) -> str:
    return url.split("?", 1)[0]


def redact_text(text: str) -> str:
    return _URL_WITH_QUERY.sub(r"\1?…", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py tests/test_logsafe.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml requirements-dev.txt sporteurope_relay tests
git commit -m "feat: scaffold add-on package with config and redacting logs"
```

---

### Task 2: HLS playlist parsing and DRM detection

**Files:**
- Create: `sporteurope_relay/relay/playlist.py`
- Test: `tests/test_playlist.py`

**Interfaces:**
- Produces: `Variant(uri: str, height: int, bandwidth: int)`; `Segment(seq: int, duration: float, uri: str, discontinuity: bool = False)`; `MediaPlaylist(target_duration: int, media_seq: int, segments: list[Segment], ended: bool)`; `parse_master(text: str, base_url: str) -> list[Variant]`; `pick_variant(variants: list[Variant], max_height: int) -> Variant` (raises `ValueError` when empty); `parse_media(text: str, base_url: str) -> MediaPlaylist`; `has_drm(text: str) -> bool`. All URIs are returned absolute.

- [ ] **Step 1: Write the failing tests**

`tests/test_playlist.py`:
```python
import pytest

from relay.playlist import Variant, has_drm, parse_master, parse_media, pick_variant

MASTER_URL = "https://stream.mux.com/PLAYBACK.m3u8?token=T"
MASTER = """#EXTM3U
#EXT-X-VERSION:5
#EXT-X-INDEPENDENT-SEGMENTS

#EXT-X-STREAM-INF:BANDWIDTH=2340800,AVERAGE-BANDWIDTH=2340800,CODECS="mp4a.40.2,avc1.640020",RESOLUTION=960x540,CLOSED-CAPTIONS=NONE
https://manifest-x.edgemv.mux.com/A/rendition.m3u8?cdn=edgemv&signature=S1
#EXT-X-STREAM-INF:BANDWIDTH=3990800,AVERAGE-BANDWIDTH=3990800,CODECS="mp4a.40.2,avc1.640020",RESOLUTION=1280x720,CLOSED-CAPTIONS=NONE
https://manifest-x.edgemv.mux.com/B/rendition.m3u8?cdn=edgemv&signature=S2
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080,CLOSED-CAPTIONS=NONE
C/rendition.m3u8?signature=S3
"""

RENDITION_URL = "https://manifest-x.edgemv.mux.com/B/rendition.m3u8?signature=S2"
LIVE = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:5
#EXT-X-MEDIA-SEQUENCE:2045
#EXT-X-PROGRAM-DATE-TIME:2026-10-02T17:00:00.000+00:00
#EXTINF:4,
https://chunk-x.edgemv.mux.com/v1/chunk/Z/2045.ts?signature=A
#EXT-X-DISCONTINUITY
#EXTINF:3.5,
chunk/2046.ts?signature=B
"""


def test_parse_master_reads_variants_with_absolute_uris():
    variants = parse_master(MASTER, MASTER_URL)
    assert [(v.height, v.bandwidth) for v in variants] == [(540, 2340800), (720, 3990800), (1080, 6000000)]
    assert variants[0].uri == "https://manifest-x.edgemv.mux.com/A/rendition.m3u8?cdn=edgemv&signature=S1"
    assert variants[2].uri == "https://stream.mux.com/C/rendition.m3u8?signature=S3"


@pytest.mark.parametrize("max_height,expected", [(1080, 1080), (900, 720), (720, 720), (360, 540)])
def test_pick_variant_takes_highest_not_above_max(max_height, expected):
    assert pick_variant(parse_master(MASTER, MASTER_URL), max_height).height == expected


def test_pick_variant_prefers_bandwidth_on_equal_height():
    variants = [Variant("a", 720, 1), Variant("b", 720, 2)]
    assert pick_variant(variants, 1080).uri == "b"


def test_pick_variant_rejects_empty_master():
    with pytest.raises(ValueError):
        pick_variant([], 1080)


def test_parse_media_numbers_segments_from_media_sequence():
    media = parse_media(LIVE, RENDITION_URL)
    assert (media.target_duration, media.media_seq, media.ended) == (5, 2045, False)
    assert [(s.seq, s.duration, s.discontinuity) for s in media.segments] == [(2045, 4.0, False), (2046, 3.5, True)]
    assert media.segments[1].uri == "https://manifest-x.edgemv.mux.com/B/chunk/2046.ts?signature=B"


def test_parse_media_detects_endlist():
    assert parse_media(LIVE + "#EXT-X-ENDLIST\n", RENDITION_URL).ended is True


@pytest.mark.parametrize(
    "line,expected",
    [
        ('#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://k",KEYFORMAT="com.apple.streamingkeydelivery"', True),
        ('#EXT-X-KEY:METHOD=AES-128,URI="https://k.example/key"', True),
        ('#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="skd://k"', True),
        ('#EXT-X-KEY:METHOD=NONE', False),
    ],
)
def test_has_drm(line, expected):
    assert has_drm(LIVE.replace("#EXT-X-TARGETDURATION:5", "#EXT-X-TARGETDURATION:5\n" + line)) is expected


def test_plain_playlists_have_no_drm():
    assert has_drm(MASTER) is False and has_drm(LIVE) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_playlist.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.playlist'`.

- [ ] **Step 3: Implement**

`sporteurope_relay/relay/playlist.py`:
```python
"""Minimal HLS parsing for Mux master/media playlists."""
import re
from dataclasses import dataclass
from urllib.parse import urljoin

_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


@dataclass(frozen=True)
class Variant:
    uri: str
    height: int
    bandwidth: int


@dataclass(frozen=True)
class Segment:
    seq: int
    duration: float
    uri: str
    discontinuity: bool = False


@dataclass(frozen=True)
class MediaPlaylist:
    target_duration: int
    media_seq: int
    segments: list[Segment]
    ended: bool


def _attrs(line: str) -> dict[str, str]:
    body = line.split(":", 1)[1] if ":" in line else ""
    return {key: value.strip('"') for key, value in _ATTR.findall(body)}


def parse_master(text: str, base_url: str) -> list[Variant]:
    variants: list[Variant] = []
    pending: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF"):
            pending = _attrs(line)
        elif line and not line.startswith("#") and pending is not None:
            resolution = pending.get("RESOLUTION", "")
            height = int(resolution.split("x")[1]) if "x" in resolution else 0
            variants.append(Variant(urljoin(base_url, line), height, int(pending.get("BANDWIDTH", "0"))))
            pending = None
    return variants


def pick_variant(variants: list[Variant], max_height: int) -> Variant:
    if not variants:
        raise ValueError("master playlist has no variants")
    fitting = [v for v in variants if v.height <= max_height]
    pool = fitting or [min(variants, key=lambda v: (v.height, v.bandwidth))]
    return max(pool, key=lambda v: (v.height, v.bandwidth))


def parse_media(text: str, base_url: str) -> MediaPlaylist:
    target, media_seq, ended = 6, 0, False
    segments: list[Segment] = []
    duration: float | None = None
    discontinuity = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-TARGETDURATION:"):
            target = int(float(line.split(":", 1)[1]))
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media_seq = int(line.split(":", 1)[1])
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",")[0])
        elif line == "#EXT-X-DISCONTINUITY":
            discontinuity = True
        elif line == "#EXT-X-ENDLIST":
            ended = True
        elif line and not line.startswith("#") and duration is not None:
            segments.append(Segment(media_seq + len(segments), duration, urljoin(base_url, line), discontinuity))
            duration, discontinuity = None, False
    return MediaPlaylist(target, media_seq, segments, ended)


def has_drm(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(("#EXT-X-KEY", "#EXT-X-SESSION-KEY")):
            attrs = _attrs(line)
            if attrs.get("METHOD", "NONE") != "NONE" or "KEYFORMAT" in attrs:
                return True
    return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_playlist.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add sporteurope_relay/relay/playlist.py tests/test_playlist.py
git commit -m "feat: parse Mux HLS playlists and detect DRM"
```

---

### Task 3: Segment buffer and relay playlist rendering

**Files:**
- Create: `sporteurope_relay/relay/segment_buffer.py`
- Modify: `sporteurope_relay/relay/playlist.py` (append `render_media`)
- Test: `tests/test_segment_buffer.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `BufferedSegment(seq: int, duration: float, data: bytes, discontinuity: bool, disc_seq: int)`; `SegmentBuffer(max_seconds: float = 120.0)` with `append(duration: float, data: bytes, discontinuity: bool = False) -> int` (returns the local seq), `get(seq: int) -> bytes | None`, `window(count: int) -> list[BufferedSegment]`, `reset() -> None`, `__len__`. Local numbering starts at 0 and never goes backwards, even across `reset()`. `playlist.render_media(segments: list[BufferedSegment], target_duration: int, ended: bool = False) -> str` produces segment URIs `seg/{seq}.ts`.

- [ ] **Step 1: Write the failing tests**

`tests/test_segment_buffer.py`:
```python
from relay.playlist import render_media
from relay.segment_buffer import SegmentBuffer


def test_append_numbers_segments_and_serves_them():
    buf = SegmentBuffer()
    assert buf.append(4.0, b"a") == 0
    assert buf.append(4.0, b"b") == 1
    assert buf.get(1) == b"b"
    assert buf.get(7) is None
    assert len(buf) == 2


def test_prunes_oldest_beyond_max_seconds():
    buf = SegmentBuffer(max_seconds=10)
    for i in range(5):
        buf.append(4.0, bytes([i]))
    assert [s.seq for s in buf.window(10)] == [3, 4]
    assert buf.get(0) is None


def test_window_returns_newest_segments():
    buf = SegmentBuffer()
    for i in range(5):
        buf.append(4.0, bytes([i]))
    assert [s.seq for s in buf.window(2)] == [3, 4]


def test_reset_keeps_numbering_and_marks_discontinuity():
    buf = SegmentBuffer()
    buf.append(4.0, b"a")
    buf.append(4.0, b"b")
    buf.reset()
    assert len(buf) == 0 and buf.get(1) is None
    assert buf.append(4.0, b"c") == 2
    first = buf.window(1)[0]
    assert first.discontinuity and first.disc_seq == 1
    assert buf.append(4.0, b"d") == 3
    assert not buf.window(1)[0].discontinuity


def test_first_segment_ever_is_not_a_discontinuity():
    buf = SegmentBuffer()
    buf.reset()
    buf.append(4.0, b"a")
    assert not buf.window(1)[0].discontinuity


def test_render_media_points_segments_at_relay():
    buf = SegmentBuffer()
    buf.append(4.0, b"a")
    buf.append(3.5, b"b")
    assert render_media(buf.window(10), target_duration=4) == (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXT-X-DISCONTINUITY-SEQUENCE:0\n#EXTINF:4.000,\nseg/0.ts\n#EXTINF:3.500,\nseg/1.ts\n"
    )


def test_render_media_after_reset_uses_discontinuity_sequence_header():
    buf = SegmentBuffer()
    buf.append(4.0, b"a")
    buf.reset()
    buf.append(4.0, b"b")
    text = render_media(buf.window(10), target_duration=4)
    assert "#EXT-X-MEDIA-SEQUENCE:1\n#EXT-X-DISCONTINUITY-SEQUENCE:1\n" in text
    assert "#EXT-X-DISCONTINUITY\n" not in text


def test_render_media_tags_discontinuity_inside_window():
    buf = SegmentBuffer()
    buf.append(4.0, b"a")
    buf.append(4.0, b"b", discontinuity=True)
    text = render_media(buf.window(10), target_duration=4)
    assert "seg/0.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4.000,\nseg/1.ts" in text
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:0" in text


def test_render_media_raises_target_duration_to_longest_segment_and_ends():
    buf = SegmentBuffer()
    buf.append(6.2, b"a")
    text = render_media(buf.window(10), target_duration=4, ended=True)
    assert "#EXT-X-TARGETDURATION:7" in text and text.endswith("#EXT-X-ENDLIST\n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_segment_buffer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.segment_buffer'`.

- [ ] **Step 3: Implement**

`sporteurope_relay/relay/segment_buffer.py`:
```python
"""Segments held in memory for every TV, numbered locally so numbering never goes backwards."""
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class BufferedSegment:
    seq: int
    duration: float
    data: bytes
    discontinuity: bool
    disc_seq: int


class SegmentBuffer:
    def __init__(self, max_seconds: float = 120.0):
        self.max_seconds = max_seconds
        self._segments: OrderedDict[int, BufferedSegment] = OrderedDict()
        self._next_seq = 0
        self._disc_seq = 0
        self._pending_discontinuity = False

    def __len__(self) -> int:
        return len(self._segments)

    def append(self, duration: float, data: bytes, discontinuity: bool = False) -> int:
        discontinuity = discontinuity or self._pending_discontinuity
        self._pending_discontinuity = False
        if discontinuity:
            self._disc_seq += 1
        seq = self._next_seq
        self._next_seq += 1
        self._segments[seq] = BufferedSegment(seq, duration, data, discontinuity, self._disc_seq)
        self._prune()
        return seq

    def get(self, seq: int) -> bytes | None:
        segment = self._segments.get(seq)
        return segment.data if segment else None

    def window(self, count: int) -> list[BufferedSegment]:
        return list(self._segments.values())[-count:]

    def reset(self) -> None:
        """Drop everything; the next segment starts a discontinuity, numbering continues."""
        self._segments.clear()
        self._pending_discontinuity = self._next_seq > 0

    def _prune(self) -> None:
        total = sum(s.duration for s in self._segments.values())
        while len(self._segments) > 1 and total > self.max_seconds:
            _, oldest = self._segments.popitem(last=False)
            total -= oldest.duration
```

Append to `sporteurope_relay/relay/playlist.py` (add `import math` to the imports at the top):
```python
def render_media(segments, target_duration: int, ended: bool = False) -> str:
    """Relay playlist; `segments` are BufferedSegment-like (seq, duration, discontinuity, disc_seq)."""
    target = max([target_duration] + [math.ceil(s.duration) for s in segments])
    first = segments[0] if segments else None
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target}",
        f"#EXT-X-MEDIA-SEQUENCE:{first.seq if first else 0}",
        f"#EXT-X-DISCONTINUITY-SEQUENCE:{first.disc_seq if first else 0}",
    ]
    for segment in segments:
        if segment.discontinuity and segment is not first:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{segment.duration:.3f},")
        lines.append(f"seg/{segment.seq}.ts")
    if ended:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_segment_buffer.py tests/test_playlist.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add sporteurope_relay/relay/segment_buffer.py sporteurope_relay/relay/playlist.py tests/test_segment_buffer.py
git commit -m "feat: in-memory segment buffer and relay playlist rendering"
```

---

### Task 4: Game model, team filter and unlock rules

**Files:**
- Create: `sporteurope_relay/relay/games.py`
- Test: `tests/test_games.py`

**Interfaces:**
- Produces: `Game(id, slug, profile_slug, name, home, guest, start: datetime | None, live: bool, free: bool, unlocked: bool | None = None)` (frozen) with `to_json() -> dict` (keys `id, name, home, guest, start` (ISO or None), `live, unlocked`); `parse_games(items: list[dict], team_slug: str) -> list[Game]` (live first, then by start; deduped by id); `collect_ids(obj) -> set[str]` (every UUID found anywhere, lower-case); `is_unlocked(detail: dict, owned: set[str]) -> bool`.

- [ ] **Step 1: Write the failing tests**

`tests/test_games.py`:
```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_games.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.games'`.

- [ ] **Step 3: Implement**

`sporteurope_relay/relay/games.py`:
```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_games.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add sporteurope_relay/relay/games.py tests/test_games.py
git commit -m "feat: team game filter and unlock rules"
```

---

### Task 5: Sporteurope client (login, games, stream info) with a fake API

**Files:**
- Create: `sporteurope_relay/relay/sporteurope_client.py`, `tests/fakes.py`, `tests/conftest.py`
- Test: `tests/test_sporteurope_client.py`

**Interfaces:**
- Consumes: `Game`, `parse_games`, `collect_ids`, `is_unlocked` from Task 4.
- Produces:
  - `API_BASE = "https://api.sporteurope.tv"`, `WEB_ORIGIN = "https://sporteurope.tv"`.
  - Errors, each with a class attribute `code`: `SporteuropeError` (`"upstream"`), `UpstreamError(SporteuropeError)` (`"upstream"`), `HttpStatusError(UpstreamError)` with `.status: int`, `LoginFailed` (`"login_failed"`), `NotPurchased` (`"not_purchased"`), `DrmProtected` (`"drm"`), `StreamInUse` (`"stream_in_use"`).
  - `StreamInfo(master_url: str, drm: bool)`.
  - `SporteuropeClient(http: aiohttp.ClientSession, email: str, password: str, team_slug: str, *, base_url: str = API_BASE)` with `async login() -> None`, `async list_games() -> list[Game]` (each `Game.unlocked` set to `bool`), `async stream_info(asset_id: str) -> StreamInfo`, attributes `owned_ids: set[str]`, `login_body: dict`, class attrs `GAMES_TTL = 60.0`, `PER_PAGE = 50`, `MAX_PAGES = 10`.
  - The `http` session must use `aiohttp.CookieJar(unsafe=True)` (needed for IP hosts in tests; harmless in production).
  - Test support: `tests/fakes.py` (`FakeSporteurope`, `asset()`, id constants), `tests/conftest.py` fixtures `fake`, `http`, `client`. Later tasks reuse them.

- [ ] **Step 1: Write the fake Sporteurope + Mux server**

`tests/fakes.py`:
```python
"""In-process fake of api.sporteurope.tv and Mux, shared by client, relay and integration tests."""
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
        app.router.add_get("/api/web/public/next-livestreams", self._list)
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
        if await request.json() != {"email": EMAIL, "password": PASSWORD}:
            return web.json_response({"message": "invalid"}, status=422)
        self._session += 1
        self.session_expired = False
        resp = web.json_response(
            {"id": "user", "bought_products_and_asset_ids": {"products": list(self.owned), "assets": []}}, status=201
        )
        resp.set_cookie("session", f"s{self._session}")
        return resp

    async def _list(self, request):
        self.calls["list"] += 1
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
        height = request.match_info["height"]
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:1", f"#EXT-X-MEDIA-SEQUENCE:{self.media_seq}"]
        if self.rendition_key:
            lines.append(self.rendition_key)
        for seq in range(self.media_seq, self.media_seq + self.live_segments):
            lines += ["#EXTINF:1.000,", f"/mux/chunk/{height}/{seq}.ts?signature=t{self._token}"]
        return web.Response(text="\n".join(lines) + "\n")

    async def _chunk(self, request):
        key = (request.match_info["height"], int(request.match_info["seq"]))
        self.segment_fetches[key] += 1
        return web.Response(body=f"seg-{key[0]}-{key[1]}".encode(), content_type="video/mp2t")
```

`tests/conftest.py`:
```python
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
```

- [ ] **Step 2: Write the failing client tests**

`tests/test_sporteurope_client.py`:
```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sporteurope_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.sporteurope_client'`.

- [ ] **Step 4: Implement**

`sporteurope_relay/relay/sporteurope_client.py`:
```python
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
        self._unlocked: dict[str, bool] = {}
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
        now = time.monotonic()
        if self._games and now - self._games[0] < self.GAMES_TTL:
            return self._games[1]
        if not self._logged_in:
            await self.login()
        items: list[dict] = []
        for page in range(1, self.MAX_PAGES + 1):
            body = await self._request("GET", "/api/web/public/next-livestreams",
                                       params={"page": page, "per_page": self.PER_PAGE, "lang": "de"})
            items += body.get("data") or []
            if page >= int((body.get("meta") or {}).get("last_page", page)):
                break
        games = [await self._with_unlock(game) for game in parse_games(items, self._team_slug)]
        self._games = (now, games)
        return games

    async def _with_unlock(self, game: Game) -> Game:
        if game.id not in self._unlocked:
            if game.free or game.id.lower() in self.owned_ids:
                self._unlocked[game.id] = True
            else:
                detail = await self._request("GET", f"/api/web/public/assets/{game.profile_slug}/{game.slug}",
                                             params={"lang": "de"})
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sporteurope_client.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add sporteurope_relay/relay/sporteurope_client.py tests/fakes.py tests/conftest.py tests/test_sporteurope_client.py
git commit -m "feat: Sporteurope client with session handling and unlock status"
```

---

### Task 6: Smoke check against the real account (manual, with the user)

This checks the assumptions the fake cannot: the login response shape, whether `next-livestreams` contains the Kassel games, and whether the unlock rule matches what the website shows. It prints no secrets.

**Files:**
- Create: `tools/smoke.py`

**Interfaces:**
- Consumes: `SporteuropeClient` (`login`, `list_games`, `stream_info`, `owned_ids`, `login_body`), `playlist.parse_master/pick_variant/has_drm`, `logsafe.redact_url`.

- [ ] **Step 1: Write the script**

`tools/smoke.py`:
```python
"""Manual check against the real Sporteurope account. Prints no password, tokens or signed URLs.

Usage:
  SPORTEUROPE_EMAIL=... SPORTEUROPE_PASSWORD=... .venv/bin/python tools/smoke.py [--asset-id ID] [--shapes]
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sporteurope_relay"))

import aiohttp  # noqa: E402

from relay import playlist  # noqa: E402
from relay.logsafe import redact_url, setup_logging  # noqa: E402
from relay.sporteurope_client import SporteuropeClient  # noqa: E402


def shape(value, depth=0):
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"… {len(value)} items"] if value else []
    return type(value).__name__ if value is not None else None


async def main(args):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
        client = SporteuropeClient(http, os.environ["SPORTEUROPE_EMAIL"], os.environ["SPORTEUROPE_PASSWORD"], args.team)
        await client.login()
        print(f"owned product/asset ids: {len(client.owned_ids)}")
        if args.shapes:
            print(json.dumps(shape(client.login_body), indent=1))
        for game in await client.list_games():
            when = "LIVE" if game.live else (game.start.strftime("%a %d.%m. %H:%M UTC") if game.start else "?")
            lock = "unlocked" if game.unlocked else "NOT PURCHASED"
            print(f"{when:22} {lock:14} {game.home} – {game.guest}  id={game.id}")
        if args.asset_id:
            info = await client.stream_info(args.asset_id)
            print("tokens.drm set:", info.drm)
            async with http.get(info.master_url) as resp:
                master = await resp.text()
            print("master has DRM tags:", playlist.has_drm(master))
            variant = playlist.pick_variant(playlist.parse_master(master, info.master_url), 1080)
            print("chosen variant:", variant.height, redact_url(variant.uri))
            async with http.get(variant.uri) as resp:
                rendition = await resp.text()
            print("rendition has DRM tags:", playlist.has_drm(rendition))
            print("live playlist:", "#EXT-X-ENDLIST" not in rendition, "| segments:", rendition.count("#EXTINF"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", default="ec-kassel-huskies")
    parser.add_argument("--asset-id")
    parser.add_argument("--shapes", action="store_true")
    setup_logging()
    asyncio.run(main(parser.parse_args()))
```

- [ ] **Step 2: Ask the user to run it with their credentials** (the password must not go into chat or shell history; suggest `read -s SPORTEUROPE_PASSWORD; export SPORTEUROPE_PASSWORD`)

Run: `SPORTEUROPE_EMAIL=… .venv/bin/python tools/smoke.py --shapes`
Expected:
- `owned product/asset ids:` is greater than 0 and the login shape shows where the ids live.
- The list contains `EC Kassel Huskies – Eispiraten Crimmitschau` on Fri 02.10. 17:00 UTC (19:00 local), marked `unlocked`.

- [ ] **Step 3: Resolve mismatches before continuing**

- **Friday game missing from the list:** `next-livestreams` does not reach it within `MAX_PAGES × PER_PAGE`. Raise `MAX_PAGES` to 30 in `sporteurope_client.py` and re-run. If it is still missing, stop and report the shape of the team profile endpoint (`/api/web/public/profiles/{profile_id}/assets`, seen in the HAR) to the user before changing the listing source.
- **A game the website plays is marked `NOT PURCHASED`:** the owned ids from the login response do not contain that asset's product ids (for example, the subscription is listed under a different id). Apply this fallback in `SporteuropeClient._with_unlock`, replacing the `is_unlocked` line:
  ```python
  self._unlocked[game.id] = is_unlocked(detail, self.owned_ids) or (
      "SUBSCRIPTION" in (detail.get("monetizations") or []) and bool(self.owned_ids)
  )
  ```
  Then add this test to `tests/test_sporteurope_client.py`, run `.venv/bin/pytest -q`, and commit:
  ```python
  async def test_subscription_games_unlocked_for_accounts_with_any_purchase(fake, client):
      fake.add_asset(asset(LOCKED_ID, "kassel-sub", monetizations=("SUBSCRIPTION",)), [OTHER_PRODUCT])
      assert [g.unlocked for g in await client.list_games()] == [True]
  ```
  With this fallback, `LOCKED_ID` in `test_list_games_filters_sorts_and_marks_unlock` becomes unlocked, because its fake monetizations include `SUBSCRIPTION`. Change that fixture line to `asset(LOCKED_ID, "kassel-locked", monetizations=("PAY_PER_VIEW",), start="2026-10-04T16:00:00Z")` so the test still covers a locked game.
  Note the trade-off for the user: a game the account really lacks will then only show "🔒 Nicht gekauft" after it is selected (stream info returns 403).

- [ ] **Step 4: If a Kassel game is live (or on Friday before the game), run the stream check**

Run: `.venv/bin/python tools/smoke.py --asset-id <id of the live game>`
Expected: `tokens.drm set: False`, `master has DRM tags: False`, `rendition has DRM tags: False`, `live playlist: True`. If any DRM value is `True`, stop the project (spec non-goal).

- [ ] **Step 5: Commit**

```bash
git add tools/smoke.py
git commit -m "chore: manual smoke check against the real account"
```

---

### Task 7: HLS relay (single upstream session)

**Files:**
- Create: `sporteurope_relay/relay/hls_relay.py`
- Test: `tests/test_hls_relay.py`, `tests/stubs.py` (only `make_game` in this task)

**Interfaces:**
- Consumes: `SporteuropeClient.stream_info`, errors and `WEB_ORIGIN` (Task 5); `playlist.parse_master/pick_variant/parse_media/has_drm/render_media` (Tasks 2–3); `SegmentBuffer` (Task 3); `Game` (Task 4); `redact_url` (Task 1).
- Produces: `HlsRelay(client, http, *, max_height=1080, idle_timeout=120.0, refresh_interval=60.0, poll_interval: float | None = None, window=10, initial_segments=3, buffer_seconds=120.0, max_outage=30.0, viewer_ttl=30.0)` with:
  - `async start(game: Game) -> None`: raises a `SporteuropeError` subclass on failure (state becomes `"error"`, `error` = its `code`).
  - `async stop() -> None`
  - `touch(client_ip: str) -> None`
  - `playlist_text() -> str | None` (None unless live with at least one segment)
  - `segment(seq: int) -> bytes | None`
  - `viewers() -> int`
  - `status() -> {"state": "idle"|"starting"|"live"|"error", "game": dict|None, "viewers": int, "error": str|None}`
  - attributes `game: Game | None`, `state: str`, `error: str | None`.

- [ ] **Step 1: Write the test helper**

`tests/stubs.py`:
```python
from relay.games import Game


def make_game(game_id, *, live=True, unlocked=True, name="Kassel – Crimmitschau"):
    return Game(id=game_id, slug=f"slug-{game_id}", profile_slug="del2", name=name, home="EC Kassel Huskies",
                guest="Eispiraten Crimmitschau", start=None, live=live, free=False, unlocked=unlocked)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_hls_relay.py`:
```python
import asyncio

import pytest

from fakes import LIVE_ID
from relay.hls_relay import HlsRelay
from relay.sporteurope_client import DrmProtected
from stubs import make_game

FAST = {"poll_interval": 0.02}


async def until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


@pytest.fixture
async def relay(client, http):
    r = HlsRelay(client, http, idle_timeout=5.0, **FAST)
    yield r
    await r.stop()


async def test_start_fetches_only_newest_segments_of_best_variant(fake, relay):
    fake.live_segments = 20  # long DVR window
    await relay.start(make_game(LIVE_ID))
    assert relay.state == "live"
    assert sorted(fake.segment_fetches) == [("1080", 117), ("1080", 118), ("1080", 119)]
    text = relay.playlist_text()
    assert text.count("seg/") == 3
    assert "mux" not in text and "signature" not in text


async def test_max_height_limits_variant(fake, client, http):
    relay = HlsRelay(client, http, max_height=720, **FAST)
    await relay.start(make_game(LIVE_ID))
    await relay.stop()
    assert {height for height, _ in fake.segment_fetches} == {"540"}


async def test_each_upstream_segment_is_fetched_once(fake, relay):
    await relay.start(make_game(LIVE_ID))
    for _ in range(5):
        fake.advance(1)
        newest = fake.media_seq + fake.live_segments - 1
        await until(lambda: ("1080", newest) in fake.segment_fetches)
    await asyncio.sleep(0.1)
    assert set(fake.segment_fetches.values()) == {1}
    assert len(fake.segment_fetches) == 3 + 5


async def test_serves_buffered_segments(fake, relay):
    await relay.start(make_game(LIVE_ID))
    assert relay.segment(0) == b"seg-1080-101"
    assert relay.segment(2) == b"seg-1080-103"
    assert relay.segment(99) is None


async def test_drm_token_is_refused_before_touching_mux(fake, relay):
    fake.drm_token = "drm-jwt"
    with pytest.raises(DrmProtected):
        await relay.start(make_game(LIVE_ID))
    assert (relay.state, relay.error) == ("error", "drm")
    assert fake.calls["master"] == 0 and not fake.segment_fetches


async def test_encrypted_rendition_is_refused(fake, relay):
    fake.rendition_key = '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://k",KEYFORMAT="com.apple.streamingkeydelivery"'
    with pytest.raises(DrmProtected):
        await relay.start(make_game(LIVE_ID))
    assert not fake.segment_fetches
    assert relay.playlist_text() is None


async def test_expired_mux_token_refreshes_stream_info_once(fake, relay):
    await relay.start(make_game(LIVE_ID))
    before = fake.calls["stream_info"]
    fake.mux_forbidden = 1
    fake.advance(1)
    await until(lambda: ("1080", 104) in fake.segment_fetches)
    assert fake.calls["stream_info"] == before + 1
    assert relay.state == "live"


async def test_repeated_mux_rejection_stops_without_fighting(fake, relay):
    await relay.start(make_game(LIVE_ID))
    fake.mux_forbidden = 2
    await until(lambda: relay.state == "error")
    assert relay.error == "stream_in_use"
    polls = fake.calls["rendition"]
    await asyncio.sleep(0.2)
    assert fake.calls["rendition"] == polls


async def test_lost_access_on_refresh_is_stream_in_use(fake, client, http):
    relay = HlsRelay(client, http, refresh_interval=0.05, **FAST)
    await relay.start(make_game(LIVE_ID))
    fake.stream_status = 403
    await until(lambda: relay.state == "error")
    assert relay.error == "stream_in_use"
    await relay.stop()


async def test_upstream_outage_gives_up_after_max_outage(fake, client, http):
    relay = HlsRelay(client, http, max_outage=0.0, **FAST)
    await relay.start(make_game(LIVE_ID))
    fake.rendition_status = 500
    await until(lambda: relay.state == "error", timeout=5.0)
    assert relay.error == "upstream"
    await relay.stop()


async def test_idle_timeout_releases_upstream(fake, client, http):
    relay = HlsRelay(client, http, idle_timeout=0.2, **FAST)
    await relay.start(make_game(LIVE_ID))
    await until(lambda: relay.state == "idle")
    assert relay.game is None and relay.playlist_text() is None
    polls = fake.calls["rendition"]
    await asyncio.sleep(0.1)
    assert fake.calls["rendition"] == polls


async def test_viewer_requests_keep_relay_alive(fake, client, http):
    relay = HlsRelay(client, http, idle_timeout=0.2, **FAST)
    await relay.start(make_game(LIVE_ID))
    for _ in range(10):
        relay.touch("192.168.0.50")
        await asyncio.sleep(0.05)
    assert relay.state == "live"
    assert relay.viewers() == 1
    assert relay.status()["game"]["id"] == LIVE_ID
    await relay.stop()


async def test_sequence_regression_restarts_buffer_with_discontinuity(fake, relay):
    await relay.start(make_game(LIVE_ID))
    fake.media_seq = 5  # encoder restart: upstream numbering drops
    await until(lambda: ("1080", 8) in fake.segment_fetches)
    text = relay.playlist_text()
    assert "#EXT-X-MEDIA-SEQUENCE:3\n#EXT-X-DISCONTINUITY-SEQUENCE:1\n" in text
    assert relay.segment(3) == b"seg-1080-6"


async def test_switching_game_keeps_local_numbering(fake, relay):
    await relay.start(make_game(LIVE_ID))
    await relay.start(make_game(LIVE_ID, name="Anderes Spiel"))
    text = relay.playlist_text()
    assert "#EXT-X-MEDIA-SEQUENCE:3\n#EXT-X-DISCONTINUITY-SEQUENCE:1\n" in text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_hls_relay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.hls_relay'`.

- [ ] **Step 4: Implement**

`sporteurope_relay/relay/hls_relay.py`:
```python
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


class MuxForbidden(Exception):
    """Mux answered 401/403/410: the signed URL expired or access was revoked."""


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
        if self.state != "live" or len(self._buffer) == 0:
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
                try:
                    if now - self._last_refresh >= self._refresh_interval:
                        await self._refresh()
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
        except NotPurchased:
            # We were allowed to play a moment ago, so the slot was taken elsewhere.
            self._halt(StreamInUse.code)
        except SporteuropeError as exc:
            self._halt(exc.code)

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
        variants = playlist.parse_master(master, info.master_url)
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
        media = playlist.parse_media(text, self._rendition_url)
        self._target = max(1, media.target_duration)
        self._ended = media.ended
        if not media.segments:
            return
        if self._upstream_last is not None and media.segments[-1].seq < self._upstream_last:
            log.warning("Upstream media sequence went backwards, restarting buffer")
            self._buffer.reset()
            self._upstream_last = None
        last = self._upstream_last
        new = [s for s in media.segments if last is None or s.seq > last]
        if last is None:
            new = new[-self._initial_segments:]
        for seg in new:
            data = await self._fetch(seg.uri)
            self._buffer.append(seg.duration, data, discontinuity=seg.discontinuity)
            self._upstream_last = seg.seq

    async def _fetch(self, url: str) -> bytes:
        try:
            async with self._http.get(url, headers={"Origin": WEB_ORIGIN, "Referer": WEB_ORIGIN + "/"}) as resp:
                if resp.status in (401, 403, 410):
                    raise MuxForbidden(resp.status)
                if resp.status >= 400:
                    raise UpstreamError(f"HTTP {resp.status} für {redact_url(url)}")
                return await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise UpstreamError(f"{type(exc).__name__} für {redact_url(url)}") from exc

    async def _fetch_text(self, url: str) -> str:
        return (await self._fetch(url)).decode("utf-8", "replace")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_hls_relay.py -v`
Expected: all passed (the outage test takes about 1 s).

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add sporteurope_relay/relay/hls_relay.py tests/stubs.py tests/test_hls_relay.py
git commit -m "feat: single-upstream HLS relay with fetch-once buffer"
```

---

### Task 8: HTTP API and stream endpoints

**Files:**
- Create: `sporteurope_relay/relay/web.py`
- Modify: `tests/stubs.py` (add `StubClient`, `StubRelay`)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `client.list_games()`; relay `start/stop/touch/playlist_text/segment/status`, `.game`, `.state` (Task 7); error classes with `.code` (Task 5).
- Produces: `STATIC_DIR: Path`; `MESSAGES: dict[str, str]`; `create_app(client, relay, static_dir: Path = STATIC_DIR) -> web.Application` with routes:
  - `GET /` → `static_dir/index.html`; `GET /static/*` → files in `static_dir`
  - `GET /api/games` → `{"games": [Game.to_json()], "error": code|None, "message": str|None}`
  - `POST /api/play` body `{"game_id": str}` → 200 `relay.status()` or error JSON `{"error", "message"}`
  - `POST /api/stop` → 200 `relay.status()`
  - `GET /api/status` → `relay.status()` plus `"message"`
  - `GET /live.m3u8` → 200 `application/vnd.apple.mpegurl`, or 503 with `Retry-After: 2`
  - `GET /seg/{seq}.ts` → 200 `video/mp2t` or 404

- [ ] **Step 1: Add stubs**

Append to `tests/stubs.py`:
```python
class StubClient:
    def __init__(self, games=(), error=None):
        self.games = list(games)
        self.error = error

    async def list_games(self):
        if self.error:
            raise self.error
        return self.games


class StubRelay:
    def __init__(self):
        self.game = None
        self.state = "idle"
        self.error = None
        self.started = []
        self.fail = None
        self.text = None
        self.segments = {}
        self.touched = []

    async def start(self, game):
        if self.fail:
            self.state, self.error = "error", self.fail.code
            raise self.fail
        self.started.append(game.id)
        self.game, self.state = game, "live"

    async def stop(self):
        self.game, self.state = None, "idle"

    def touch(self, ip):
        self.touched.append(ip)

    def playlist_text(self):
        return self.text

    def segment(self, seq):
        return self.segments.get(seq)

    def viewers(self):
        return len(set(self.touched))

    def status(self):
        return {"state": self.state, "game": self.game.to_json() if self.game else None,
                "viewers": self.viewers(), "error": self.error}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_web.py`:
```python
import pytest

from relay.sporteurope_client import DrmProtected, LoginFailed
from relay.web import create_app
from stubs import StubClient, StubRelay, make_game

LIVE = make_game("live-1")
UPCOMING = make_game("soon-1", live=False)
LOCKED = make_game("locked-1", unlocked=False)


@pytest.fixture
def relay():
    return StubRelay()


@pytest.fixture
def static_dir(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Relay</title>")
    return tmp_path


@pytest.fixture
async def tv(aiohttp_client, relay, static_dir):
    return await aiohttp_client(create_app(StubClient([LIVE, UPCOMING, LOCKED]), relay, static_dir))


async def test_index_is_served(tv):
    resp = await tv.get("/")
    assert resp.status == 200 and "<title>Relay</title>" in await resp.text()


async def test_games_lists_json(tv):
    data = await (await tv.get("/api/games")).json()
    assert [g["id"] for g in data["games"]] == ["live-1", "soon-1", "locked-1"]
    assert data["error"] is None


async def test_games_reports_login_failure_in_german(aiohttp_client, relay, static_dir):
    tv = await aiohttp_client(create_app(StubClient(error=LoginFailed()), relay, static_dir))
    data = await (await tv.get("/api/games")).json()
    assert data["games"] == []
    assert data["message"] == "Login fehlgeschlagen – Zugangsdaten in HA prüfen"


async def test_play_starts_relay(tv, relay):
    resp = await tv.post("/api/play", json={"game_id": "live-1"})
    assert resp.status == 200
    assert (await resp.json())["state"] == "live"
    assert relay.started == ["live-1"]


async def test_second_tv_joins_running_game_without_restart(tv, relay):
    await tv.post("/api/play", json={"game_id": "live-1"})
    resp = await tv.post("/api/play", json={"game_id": "live-1"})
    assert resp.status == 200
    assert relay.started == ["live-1"]


@pytest.mark.parametrize("game_id,status,message", [
    ("locked-1", 403, "🔒 Nicht gekauft"),
    ("soon-1", 409, "Spiel ist noch nicht live"),
    ("nope", 404, "Spiel nicht gefunden"),
])
async def test_play_refuses(tv, relay, game_id, status, message):
    resp = await tv.post("/api/play", json={"game_id": game_id})
    assert resp.status == status
    assert (await resp.json())["message"] == message
    assert relay.started == []


async def test_play_reports_drm_in_german(tv, relay):
    relay.fail = DrmProtected()
    resp = await tv.post("/api/play", json={"game_id": "live-1"})
    assert resp.status == 403
    assert (await resp.json())["message"] == "Dieses Spiel ist DRM-geschützt – nicht unterstützt"


async def test_status_includes_message(tv, relay):
    relay.state, relay.error = "error", "stream_in_use"
    data = await (await tv.get("/api/status")).json()
    assert data["message"] == "Stream wird an anderer Stelle genutzt"


async def test_stop(tv, relay):
    await tv.post("/api/play", json={"game_id": "live-1"})
    assert (await (await tv.post("/api/stop")).json())["state"] == "idle"


async def test_live_playlist_unavailable_without_game(tv, relay):
    resp = await tv.get("/live.m3u8")
    assert resp.status == 503 and resp.headers["Retry-After"] == "2"
    assert relay.touched == ["127.0.0.1"]


async def test_live_playlist_and_segments(tv, relay):
    relay.text = "#EXTM3U\n"
    relay.segments = {7: b"ts-bytes"}
    resp = await tv.get("/live.m3u8")
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("application/vnd.apple.mpegurl")
    seg = await tv.get("/seg/7.ts")
    assert seg.status == 200 and await seg.read() == b"ts-bytes"
    assert seg.headers["Content-Type"] == "video/mp2t"
    assert (await tv.get("/seg/8.ts")).status == 404
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_web.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.web'`.

- [ ] **Step 4: Implement**

`sporteurope_relay/relay/web.py`:
```python
"""HTTP routes for the TVs: page, JSON API, relay playlist and segments."""
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
}
_HTTP_STATUS = {"login_failed": 401, "not_purchased": 403, "drm": 403, "stream_in_use": 409, "not_live": 409,
                "unknown_game": 404, "upstream": 502}
_NO_CACHE = {"Cache-Control": "no-cache"}


def _error(code: str) -> web.Response:
    return web.json_response({"error": code, "message": MESSAGES[code]}, status=_HTTP_STATUS[code])


def create_app(client, relay, static_dir: Path = STATIC_DIR) -> web.Application:
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
        if game.unlocked is False:
            return _error("not_purchased")
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
        data["message"] = MESSAGES.get(data["error"]) if data["error"] else None
        return web.json_response(data, headers=_NO_CACHE)

    async def live(request):
        relay.touch(request.remote or "?")
        text = relay.playlist_text()
        if text is None:
            return web.Response(status=503, text="Kein aktives Spiel\n", headers={"Retry-After": "2"})
        return web.Response(text=text, content_type="application/vnd.apple.mpegurl", headers=_NO_CACHE)

    async def segment(request):
        relay.touch(request.remote or "?")
        data = relay.segment(int(request.match_info["seq"]))
        if data is None:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type="video/mp2t", headers={"Cache-Control": "max-age=300"})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/games", games)
    app.router.add_post("/api/play", play)
    app.router.add_post("/api/stop", stop)
    app.router.add_get("/api/status", status)
    app.router.add_get("/live.m3u8", live)
    app.router.add_get(r"/seg/{seq:\d+}.ts", segment)
    app.router.add_static("/static/", static_dir)
    return app
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_web.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add sporteurope_relay/relay/web.py tests/stubs.py tests/test_web.py
git commit -m "feat: HTTP API, relay playlist and segment endpoints"
```

---

### Task 9: TV page (remote-friendly UI + local hls.js)

**Files:**
- Create: `sporteurope_relay/relay/static/index.html`, `sporteurope_relay/relay/static/app.js`, `sporteurope_relay/relay/static/style.css`, `sporteurope_relay/relay/static/hls.min.js` (vendored)
- Test: `tests/test_static.py`

**Interfaces:**
- Consumes: the Task 8 routes (`/api/games`, `/api/play`, `/api/status`, `/live.m3u8`) and their JSON fields (`games[].{id, home, guest, start, live, unlocked}`, `message`, `state`, `game.id`).

- [ ] **Step 1: Write the failing test**

`tests/test_static.py`:
```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_static.py -v`
Expected: FAIL (`index.html` missing → 404/500).

- [ ] **Step 3: Vendor hls.js**

Run:
```bash
mkdir -p sporteurope_relay/relay/static
curl -fsSL -o sporteurope_relay/relay/static/hls.min.js https://cdn.jsdelivr.net/npm/hls.js@1.5.20/dist/hls.min.js
head -c 200 sporteurope_relay/relay/static/hls.min.js
```
Expected: minified JavaScript (not an HTML error page), roughly 400 KB.

- [ ] **Step 4: Write the page**

`sporteurope_relay/relay/static/index.html`:
```html
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Huskies live</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main id="home">
  <h1>Huskies live</h1>
  <p id="message" role="status"></p>
  <div id="games" class="grid"></div>
  <p class="hint">VLC / Fire TV: <span id="vlc-url"></span></p>
</main>
<section id="player" hidden>
  <video id="video" autoplay playsinline></video>
  <p id="player-message" role="status"></p>
</section>
<script src="/static/hls.min.js"></script>
<script src="/static/app.js"></script>
</body>
</html>
```

`sporteurope_relay/relay/static/style.css`:
```css
:root { --bg: #0d1117; --tile: #1c2430; --text: #f0f3f6; --muted: #9aa6b2; --accent: #f5b301; --live: #ff5a5f; }
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--text); font: 28px/1.3 system-ui, -apple-system, "Segoe UI", sans-serif; }
[hidden] { display: none !important; }
main { padding: 48px 64px; }
h1 { margin: 0 0 16px; font-size: 52px; }
#message { min-height: 1.3em; color: var(--accent); margin: 0 0 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(560px, 1fr)); gap: 24px; }
.tile { display: block; width: 100%; text-align: left; padding: 28px 32px; border: 4px solid transparent; border-radius: 16px;
        background: var(--tile); color: inherit; font: inherit; cursor: pointer; transition: transform .1s; }
.tile:focus { outline: none; border-color: var(--accent); transform: scale(1.03); }
.tile .teams { font-size: 34px; font-weight: 700; }
.tile .when { color: var(--muted); margin-top: 8px; }
.tile.live .when { color: var(--live); font-weight: 700; }
.tile.locked { opacity: .55; }
.hint { color: var(--muted); font-size: 22px; margin-top: 40px; }
#player { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: #000; }
#player video { width: 100%; height: 100%; }
#player-message { position: absolute; left: 48px; bottom: 32px; color: var(--accent); margin: 0; }
```

`sporteurope_relay/relay/static/app.js` (ES5 on purpose: older Tizen/webOS browsers):
```js
(function () {
  "use strict";

  var GAMES_POLL_MS = 30000;
  var STATUS_POLL_MS = 10000;
  var BACK_KEYS = [8, 27, 461, 10009]; // Backspace, Esc, webOS back, Tizen back
  var DAYS = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];

  var home = document.getElementById("home");
  var grid = document.getElementById("games");
  var message = document.getElementById("message");
  var player = document.getElementById("player");
  var video = document.getElementById("video");
  var playerMessage = document.getElementById("player-message");

  var hls = null;
  var games = [];
  var armedId = null;
  var playingGameId = null;
  var statusTimer = null;

  document.getElementById("vlc-url").textContent = location.origin + "/live.m3u8";

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        data.httpOk = resp.ok;
        return data;
      });
    });
  }

  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function whenText(game) {
    if (game.live) return "● LIVE";
    if (!game.start) return "Termin offen";
    var start = new Date(game.start);
    var mins = Math.round((start.getTime() - Date.now()) / 60000);
    var label = DAYS[start.getDay()] + " " + pad(start.getDate()) + "." + pad(start.getMonth() + 1) + ". " +
      pad(start.getHours()) + ":" + pad(start.getMinutes());
    if (mins <= 0) return label + " · gleich";
    if (mins < 60) return label + " · in " + mins + " Min";
    if (mins < 48 * 60) return label + " · in " + Math.floor(mins / 60) + " Std " + (mins % 60) + " Min";
    return label + " · in " + Math.floor(mins / 1440) + " Tagen";
  }

  function render() {
    var focusedId = document.activeElement && document.activeElement.getAttribute("data-id");
    grid.innerHTML = "";
    if (!games.length) {
      grid.innerHTML = '<p class="hint">Keine anstehenden Spiele gefunden.</p>';
      return;
    }
    games.forEach(function (game) {
      var tile = document.createElement("button");
      tile.className = "tile" + (game.live ? " live" : "") + (game.unlocked === false ? " locked" : "");
      tile.setAttribute("data-id", game.id);
      var teams = document.createElement("div");
      teams.className = "teams";
      teams.textContent = game.home + " – " + game.guest;
      var when = document.createElement("div");
      when.className = "when";
      when.textContent = (game.unlocked === false ? "🔒 Nicht gekauft · " : "") + whenText(game) +
        (armedId === game.id && !game.live ? " · startet automatisch" : "");
      tile.appendChild(teams);
      tile.appendChild(when);
      tile.addEventListener("click", function () { select(game); });
      grid.appendChild(tile);
    });
    var target = (focusedId && grid.querySelector('[data-id="' + focusedId + '"]')) || grid.querySelector(".tile");
    if (target && player.hidden) target.focus();
  }

  function loadGames() {
    return api("GET", "/api/games").then(function (data) {
      games = data.games || [];
      message.textContent = data.message || "";
      render();
      if (armedId && player.hidden) {
        var armed = games.filter(function (g) { return g.id === armedId; })[0];
        if (armed && armed.live) {
          armedId = null;
          play(armed.id);
        }
      }
    }).catch(function () { message.textContent = "Relay nicht erreichbar"; });
  }

  function select(game) {
    if (game.unlocked === false) { message.textContent = "🔒 Nicht gekauft"; return; }
    if (!game.live) {
      armedId = game.id;
      message.textContent = "Startet automatisch, sobald das Spiel live ist.";
      render();
      return;
    }
    play(game.id);
  }

  function play(id) {
    message.textContent = "Stream wird gestartet …";
    api("POST", "/api/play", { game_id: id }).then(function (data) {
      if (!data.httpOk) { message.textContent = data.message || "Start fehlgeschlagen"; return; }
      message.textContent = "";
      openPlayer(id);
    }).catch(function () { message.textContent = "Relay nicht erreichbar"; });
  }

  function attach() {
    if (hls) { hls.destroy(); hls = null; }
    if (window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ liveSyncDurationCount: 3, manifestLoadingMaxRetry: 10, manifestLoadingRetryDelay: 2000 });
      hls.on(window.Hls.Events.ERROR, function (event, data) {
        if (!data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        setTimeout(function () { if (!player.hidden) attach(); }, 3000);
      });
      hls.loadSource("/live.m3u8");
      hls.attachMedia(video);
    } else {
      video.src = "/live.m3u8";
    }
    var started = video.play();
    if (started && started.catch) started.catch(function () {});
  }

  function openPlayer(id) {
    playingGameId = id;
    home.hidden = true;
    player.hidden = false;
    playerMessage.textContent = "";
    attach();
    clearInterval(statusTimer);
    statusTimer = setInterval(checkStatus, STATUS_POLL_MS);
  }

  function closePlayer(text) {
    clearInterval(statusTimer);
    if (hls) { hls.destroy(); hls = null; }
    video.removeAttribute("src");
    video.load();
    player.hidden = true;
    home.hidden = false;
    playingGameId = null;
    loadGames().then(function () { if (text) message.textContent = text; });
  }

  function checkStatus() {
    api("GET", "/api/status").then(function (s) {
      if (s.state === "error") { closePlayer(s.message); return; }
      if (s.state === "idle") { closePlayer("Stream beendet"); return; }
      if (s.game && s.game.id !== playingGameId) { // another TV switched the game
        playingGameId = s.game.id;
        attach();
      }
    });
  }

  function columns() {
    var tiles = grid.querySelectorAll(".tile");
    if (!tiles.length) return 1;
    var top = tiles[0].offsetTop, n = 0;
    for (var i = 0; i < tiles.length && tiles[i].offsetTop === top; i++) n++;
    return n || 1;
  }

  function moveFocus(delta) {
    var tiles = Array.prototype.slice.call(grid.querySelectorAll(".tile"));
    if (!tiles.length) return;
    var i = tiles.indexOf(document.activeElement);
    tiles[Math.max(0, Math.min(tiles.length - 1, (i < 0 ? 0 : i) + delta))].focus();
  }

  document.addEventListener("keydown", function (e) {
    if (!player.hidden) {
      if (BACK_KEYS.indexOf(e.keyCode) >= 0) { e.preventDefault(); closePlayer(); }
      return;
    }
    var cols = columns();
    if (e.keyCode === 37) moveFocus(-1);
    else if (e.keyCode === 39) moveFocus(1);
    else if (e.keyCode === 38) moveFocus(-cols);
    else if (e.keyCode === 40) moveFocus(cols);
    else return;
    e.preventDefault();
  });

  loadGames();
  setInterval(function () { if (player.hidden) loadGames(); }, GAMES_POLL_MS);
})();
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_static.py tests/test_web.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add sporteurope_relay/relay/static tests/test_static.py
git commit -m "feat: remote-friendly TV page with locally served hls.js"
```

---

### Task 10: Home Assistant status sensor

**Files:**
- Create: `sporteurope_relay/relay/ha_status.py`
- Test: `tests/test_ha_status.py`

**Interfaces:**
- Consumes: `relay.status()` (Task 7 shape; `StubRelay` from Task 8 in tests).
- Produces: `SENSOR_URL = "http://supervisor/core/api/states/sensor.sporteurope_relay"`; `HaStatus(http: aiohttp.ClientSession, token: str | None, relay, *, url: str = SENSOR_URL, interval: float = 10.0)` with `payload() -> dict`, `async publish_once() -> bool` (True when it POSTed), `async run() -> None` (loops forever).

- [ ] **Step 1: Write the failing tests**

`tests/test_ha_status.py`:
```python
import aiohttp
from aiohttp import web

from relay.ha_status import HaStatus
from stubs import StubRelay, make_game

PATH = "/core/api/states/sensor.sporteurope_relay"


async def fake_ha(aiohttp_server):
    posts = []

    async def handler(request):
        posts.append((request.headers.get("Authorization"), await request.json()))
        return web.json_response({})

    app = web.Application()
    app.router.add_post(PATH, handler)
    server = await aiohttp_server(app)
    return str(server.make_url(PATH)), posts


async def test_publishes_state_once_per_change(aiohttp_server):
    url, posts = await fake_ha(aiohttp_server)
    relay = StubRelay()
    async with aiohttp.ClientSession() as http:
        ha = HaStatus(http, "tok", relay, url=url)
        assert await ha.publish_once() is True
        assert await ha.publish_once() is False
        relay.game, relay.state = make_game("g1", name="Kassel – Crimmitschau"), "live"
        relay.touched = ["192.168.0.50", "192.168.0.51"]
        assert await ha.publish_once() is True
    assert len(posts) == 2
    auth, body = posts[1]
    assert auth == "Bearer tok"
    assert body["state"] == "live"
    assert body["attributes"]["game"] == "Kassel – Crimmitschau"
    assert body["attributes"]["viewers"] == 2
    assert posts[0][1]["state"] == "idle"


async def test_starting_is_reported_as_idle():
    relay = StubRelay()
    relay.state = "starting"
    assert HaStatus(None, "tok", relay).payload()["state"] == "idle"


async def test_without_supervisor_token_nothing_is_sent():
    assert await HaStatus(None, None, StubRelay()).publish_once() is False


async def test_ha_errors_do_not_raise(aiohttp_server):
    async with aiohttp.ClientSession() as http:
        ha = HaStatus(http, "tok", StubRelay(), url="http://127.0.0.1:9/nothing")
        assert await ha.publish_once() is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ha_status.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.ha_status'`.

- [ ] **Step 3: Implement**

`sporteurope_relay/relay/ha_status.py`:
```python
"""Mirrors the relay state into sensor.sporteurope_relay via the Supervisor's Core API proxy."""
import asyncio
import logging

import aiohttp

SENSOR_URL = "http://supervisor/core/api/states/sensor.sporteurope_relay"

log = logging.getLogger(__name__)


class HaStatus:
    def __init__(self, http: aiohttp.ClientSession | None, token: str | None, relay, *,
                 url: str = SENSOR_URL, interval: float = 10.0):
        self._http = http
        self._token = token
        self._relay = relay
        self._url = url
        self._interval = interval
        self._last: dict | None = None

    def payload(self) -> dict:
        status = self._relay.status()
        state = status["state"] if status["state"] in ("live", "error") else "idle"
        game = status["game"]
        return {
            "state": state,
            "attributes": {
                "friendly_name": "Sporteurope Relay",
                "icon": "mdi:hockey-puck",
                "game": game["name"] if game else None,
                "viewers": status["viewers"],
                "error": status["error"],
            },
        }

    async def publish_once(self) -> bool:
        if not self._token or self._http is None:
            return False
        payload = self.payload()
        if payload == self._last:
            return False
        try:
            async with self._http.post(self._url, json=payload,
                                       headers={"Authorization": f"Bearer {self._token}"}) as resp:
                if resp.status >= 400:
                    log.warning("Home Assistant rejected sensor update: HTTP %s", resp.status)
                    return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Home Assistant not reachable: %s", type(exc).__name__)
            return False
        self._last = payload
        return True

    async def run(self) -> None:
        while True:
            await self.publish_once()
            await asyncio.sleep(self._interval)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ha_status.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add sporteurope_relay/relay/ha_status.py tests/test_ha_status.py
git commit -m "feat: publish relay state as sensor.sporteurope_relay"
```

---

### Task 11: App wiring, entry point and 3-TV integration test

**Files:**
- Create: `sporteurope_relay/relay/app.py`, `sporteurope_relay/relay/__main__.py`
- Test: `tests/test_integration.py`

**Interfaces:**
- Consumes: `Config`, `load_config`, `OPTIONS_PATH`, `setup_logging` (Task 1); `SporteuropeClient`, `API_BASE` (Task 5); `HlsRelay` (Task 7); `create_app` (Task 8); `HaStatus` (Task 10).
- Produces: `async build_app(cfg: Config, *, base_url: str = API_BASE, ha_token: str | None = None, relay_kwargs: dict | None = None) -> web.Application`; `main() -> None` (reads options from `$RELAY_OPTIONS` or `/data/options.json`, `SUPERVISOR_TOKEN` from env, serves on port 8099).

- [ ] **Step 1: Write the failing integration test**

`tests/test_integration.py`:
```python
import asyncio

from fakes import EMAIL, LIVE_ID, OWNED_PRODUCT, PASSWORD, asset
from relay.app import build_app
from relay.config import Config


async def test_three_tvs_share_one_upstream_and_relay_stops_when_idle(fake, aiohttp_client):
    fake.add_asset(asset(LIVE_ID, "kassel-live", live=True), [OWNED_PRODUCT])
    app = await build_app(Config(email=EMAIL, password=PASSWORD), base_url=fake.base_url,
                          relay_kwargs={"poll_interval": 0.05, "idle_timeout": 1.0})
    tv = await aiohttp_client(app)

    resp = await tv.post("/api/play", json={"game_id": LIVE_ID})
    assert resp.status == 200, await resp.text()

    received = [{}, {}, {}]

    async def watch(store):
        for _ in range(30):
            playlist = await (await tv.get("/live.m3u8")).text()
            for line in playlist.splitlines():
                if line.startswith("seg/") and line not in store:
                    store[line] = await (await tv.get("/" + line)).read()
            await asyncio.sleep(0.05)

    async def live_edge():
        for _ in range(10):
            await asyncio.sleep(0.12)
            fake.advance(1)

    await asyncio.gather(*(watch(store) for store in received), live_edge())

    assert len(fake.segment_fetches) >= 10
    assert set(fake.segment_fetches.values()) == {1}, "every upstream segment fetched exactly once"
    assert fake.calls["login"] == 1
    for store in received:
        assert len(store) >= 8
        assert all(data.startswith(b"seg-1080-") for data in store.values())
    assert received[0]["seg/0.ts"] == received[1]["seg/0.ts"] == received[2]["seg/0.ts"]

    await asyncio.sleep(1.5)  # no TV asks anymore
    status = await (await tv.get("/api/status")).json()
    assert status["state"] == "idle"
    polls = fake.calls["rendition"]
    await asyncio.sleep(0.3)
    assert fake.calls["rendition"] == polls
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_integration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.app'`.

- [ ] **Step 3: Implement**

`sporteurope_relay/relay/app.py`:
```python
"""Wires client, relay, web routes and the HA sensor into one aiohttp app."""
import asyncio
import logging
import os
from contextlib import suppress

import aiohttp
from aiohttp import web

from relay.config import OPTIONS_PATH, Config, load_config
from relay.ha_status import HaStatus
from relay.hls_relay import HlsRelay
from relay.logsafe import setup_logging
from relay.sporteurope_client import API_BASE, SporteuropeClient
from relay.web import create_app

PORT = 8099

log = logging.getLogger(__name__)


async def build_app(cfg: Config, *, base_url: str = API_BASE, ha_token: str | None = None,
                    relay_kwargs: dict | None = None) -> web.Application:
    http = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),
                                 timeout=aiohttp.ClientTimeout(total=20))
    client = SporteuropeClient(http, cfg.email, cfg.password, cfg.team_slug, base_url=base_url)
    relay = HlsRelay(client, http, max_height=cfg.max_height, **(relay_kwargs or {}))
    ha = HaStatus(http, ha_token, relay)
    app = create_app(client, relay)

    async def on_startup(app):
        app["ha_task"] = asyncio.create_task(ha.run())

    async def on_cleanup(app):
        app["ha_task"].cancel()
        with suppress(asyncio.CancelledError):
            await app["ha_task"]
        await relay.stop()
        await http.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    setup_logging()
    cfg = load_config(os.environ.get("RELAY_OPTIONS", OPTIONS_PATH))
    log.info("Sporteurope Relay for team %s on port %d (max %dp)", cfg.team_slug, PORT, cfg.max_height)
    web.run_app(build_app(cfg, ha_token=os.environ.get("SUPERVISOR_TOKEN")), port=PORT, access_log=None,
                print=None)
```

`sporteurope_relay/relay/__main__.py`:
```python
from relay.app import main

main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_integration.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all passed, no warnings about un-awaited coroutines or unclosed sessions.

- [ ] **Step 6: Run the app locally against the real account (with the user)**

Create `options.json` in the repo root (it is gitignored) with the user's credentials. The user types them; do not echo them.
Run: `RELAY_OPTIONS=options.json .venv/bin/python -m relay`, then open `http://localhost:8099/` in a browser.
Expected: the Kassel games appear with lock state and countdown; arrow keys move the focus ring; the log shows no password, token or `?` query strings. Stop with Ctrl-C.

- [ ] **Step 7: Commit**

```bash
git add sporteurope_relay/relay/app.py sporteurope_relay/relay/__main__.py tests/test_integration.py
git commit -m "feat: wire the add-on app and verify one upstream for three TVs"
```

---

### Task 12: Add-on packaging and install on Home Assistant

**Deployment method (decided by the user):** public GitHub repository `https://github.com/nikq29/sporteurope-relay`, added to the HA add-on store. The Supervisor clones it and builds the image from `sporteurope_relay/Dockerfile`. The repo contains no secrets (`.gitignore` already excludes `*.har`, `options.json`, `.env`). Updates ship by bumping `version` in `config.yaml` and pushing.

**Files:**
- Create: `repository.yaml`, `sporteurope_relay/config.yaml`, `sporteurope_relay/Dockerfile`, `sporteurope_relay/DOCS.md`, `sporteurope_relay/.dockerignore`

- [ ] **Step 1: Write the add-on files**

`repository.yaml`:
```yaml
name: Sporteurope Relay
url: https://github.com/nikq29/sporteurope-relay
maintainer: Niko P
```

`sporteurope_relay/config.yaml`:
```yaml
name: Sporteurope Relay
version: "0.1.0"
slug: sporteurope_relay
description: Teilt einen Sporteurope-Livestream im Heimnetz auf mehrere Fernseher
arch:
  - amd64
  - aarch64
init: false
startup: application
boot: auto
homeassistant_api: true
ports:
  8099/tcp: 8099
ports_description:
  8099/tcp: TV-Seite und HLS-Stream (nur LAN)
watchdog: "http://[HOST]:[PORT:8099]/api/status"
options:
  email: ""
  password: ""
  team_slug: ec-kassel-huskies
  max_height: 1080
schema:
  email: email
  password: password
  team_slug: str
  max_height: int(240,2160)
```

`sporteurope_relay/Dockerfile`:
```dockerfile
FROM python:3.13-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY relay ./relay
CMD ["python", "-m", "relay"]
```

`sporteurope_relay/.dockerignore`:
```
__pycache__
*.pyc
```

`sporteurope_relay/DOCS.md`:
```markdown
# Sporteurope Relay

Holt **einen** Sporteurope-Livestream und verteilt ihn im Heimnetz an mehrere Fernseher.

## Einrichtung
1. E-Mail und Passwort des Sporteurope-Kontos eintragen, speichern, Add-on starten.
2. Fernseher:
   - **Fire TV / Android TV (VLC):** Netzwerkstream `http://192.168.0.90:8099/live.m3u8`
   - **Samsung / LG (Browser):** Lesezeichen `http://192.168.0.90:8099/`

## Grenzen
- Nur im Heimnetz. Port 8099 **nicht** im Cloudflared-Add-on freigeben.
- Immer nur ein Spiel gleichzeitig; ein Spielwechsel gilt für alle Fernseher.
- DRM-geschützte Streams werden nicht unterstützt.
- Ohne Zuschauer beendet das Add-on den Stream nach 2 Minuten.
```

- [ ] **Step 2: Verify the image builds (if Docker is available locally)**

Run: `docker build -t sporteurope-relay-test sporteurope_relay && docker run --rm sporteurope-relay-test python -c "import relay.app"`
Expected: build succeeds; the import prints nothing and exits 0. If Docker is not installed, skip this step and rely on the HA build in Step 4. Say in the report that it was skipped.

- [ ] **Step 3: Commit**

```bash
git add repository.yaml sporteurope_relay/config.yaml sporteurope_relay/Dockerfile sporteurope_relay/.dockerignore sporteurope_relay/DOCS.md
git commit -m "feat: package as Home Assistant add-on"
```

- [ ] **Step 4: Publish the repository**

Check first that nothing sensitive is tracked:
```bash
git ls-files | grep -Ei '\.har$|options\.json|\.env' && echo "STOP: sensitive file tracked" || echo clean
```
Expected: `clean`. Then:
```bash
gh repo create nikq29/sporteurope-relay --public --source . --remote origin --push
```
Expected: the repo exists at `https://github.com/nikq29/sporteurope-relay` with `repository.yaml` at the root.

- [ ] **Step 5: Install on Home Assistant (with the user)**

1. HA → Settings → Add-ons → Add-on Store → ⋮ → *Repositories* → add `https://github.com/nikq29/sporteurope-relay` → close.
2. ⋮ → *Check for updates*. **Sporteurope Relay** appears under the repository name. Install it (HA builds the image; takes a few minutes).
3. Configuration tab: enter email and password, save, start.
4. Open `http://192.168.0.90:8099/` from a laptop on the LAN.

Expected: the add-on log shows `Sporteurope Relay for team ec-kassel-huskies on port 8099` and, after the page loads, `Logged in to Sporteurope`. The page lists the Kassel games. `sensor.sporteurope_relay` exists with state `idle` (Developer Tools → States). The Cloudflared add-on config is unchanged.

Later updates: bump `version` in `sporteurope_relay/config.yaml`, commit, `git push`; in HA, *Check for updates* and then *Update* on the add-on.

---

### Task 13: Live verification — Fri 2 Oct 2026, 19:00, Kassel vs. Crimmitschau (manual, with the user)

- [ ] **Step 1: ~18:45 — pre-check**

Run `tools/smoke.py --asset-id <game id>` once the game shows `LIVE` (Task 6 Step 4). Expected: no DRM anywhere. If DRM is present, do not start the relay.

- [ ] **Step 2: Start on the first TV**

Samsung/LG browser → `http://192.168.0.90:8099/` → select the game (it starts automatically if armed before 19:00). Expected: video plays within ~10 s; `sensor.sporteurope_relay` = `live`, `viewers` = 1.

- [ ] **Step 3: Join with the second TV**

Fire TV → VLC → network stream `http://192.168.0.90:8099/live.m3u8`. Expected: both play at once; `viewers` = 2; the add-on log shows no second `Relay live:` line (no restart).

- [ ] **Step 4: Observe for at least 20 minutes**

Expected: no stalls longer than a few seconds, no `Signed Mux URL rejected` loop; at most one `session expired` re-login. If the relay stops with "Stream wird an anderer Stelle genutzt", write down the time and the preceding log lines. That shows how Sporteurope enforces the one-stream limit (open question in the spec).

- [ ] **Step 5: Idle stop**

Turn both TVs off. Expected: about 2 minutes later the log shows `No viewer for 120 s` and the sensor returns to `idle`.

- [ ] **Step 6: Record the findings**

Add a short "Live test 2026-10-02" section to the spec with the results: DRM status, stability, and any concurrency-limit behaviour. Commit it:
```bash
git add docs/superpowers/specs/2026-09-28-sporteurope-relay-design.md
git commit -m "docs: record live test results"
```
