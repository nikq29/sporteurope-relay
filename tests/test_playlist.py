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
