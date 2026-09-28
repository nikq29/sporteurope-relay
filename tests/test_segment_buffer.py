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
