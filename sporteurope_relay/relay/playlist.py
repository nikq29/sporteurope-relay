"""Minimal HLS parsing for Mux master/media playlists."""
import math
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
