# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import pytest

import m3u8
from m3u8 import KeyFrame, build_iframe_playlist

# multiple consecutive ranges inside a single file: the second and third
# are contiguous (offset omittable), the fourth restarts at a new offset
MULTI_SEGMENT_SAME_URI_KEYFRAMES = [
    KeyFrame(uri="segment1.ts", byte_offset=376, byte_length=9400, duration=4.12),
    KeyFrame(uri="segment1.ts", byte_offset=9776, byte_length=7144, duration=3.56),
    KeyFrame(uri="segment1.ts", byte_offset=16920, byte_length=8024, duration=3.82),
    KeyFrame(uri="segment1.ts", byte_offset=47000, byte_length=10340, duration=4.0),
]

# ranges spread over two different files, including a contiguous run
# after switching back to the first file
DUAL_URI_KEYFRAMES = [
    KeyFrame(uri="segment1.ts", byte_offset=376, byte_length=9400, duration=4.12),
    KeyFrame(uri="segment2.ts", byte_offset=1880, byte_length=10340, duration=3.82),
    KeyFrame(uri="segment2.ts", byte_offset=12220, byte_length=6600, duration=2.5),
    KeyFrame(uri="segment1.ts", byte_offset=9776, byte_length=7144, duration=3.56),
]


def keyframes_from_playlist(playlist):
    """Rebuilds KeyFrame records from a parsed playlist, resolving
    omitted EXT-X-BYTERANGE offsets against the previous segment."""
    frames = []
    previous = None
    for segment in playlist.segments:
        length, at, offset = segment.byterange.partition("@")
        if at:
            offset = int(offset)
        else:
            offset = previous.byte_offset + previous.byte_length
        previous = KeyFrame(
            uri=segment.uri,
            byte_offset=offset,
            byte_length=int(length),
            duration=segment.duration,
        )
        frames.append(previous)
    return frames


def extinf_durations(content):
    return [
        float(line.split(":", 1)[1].rstrip(","))
        for line in content.splitlines()
        if line.startswith("#EXTINF:")
    ]


def byterange_lines(content):
    return [
        line.split(":", 1)[1]
        for line in content.splitlines()
        if line.startswith("#EXT-X-BYTERANGE:")
    ]


def test_build_iframe_playlist_with_multiple_segments_of_same_uri():
    playlist = build_iframe_playlist(MULTI_SEGMENT_SAME_URI_KEYFRAMES)
    content = playlist.dumps()

    assert content.startswith("#EXTM3U\n")
    assert "#EXT-X-VERSION:4\n" in content
    assert "#EXT-X-TARGETDURATION:5\n" in content
    assert "#EXT-X-PLAYLIST-TYPE:VOD\n" in content
    assert "#EXT-X-I-FRAMES-ONLY\n" in content
    assert content.rstrip().endswith("#EXT-X-ENDLIST")
    # media sequence 0 is the default and must not be emitted
    assert "#EXT-X-MEDIA-SEQUENCE" not in content

    # contiguous ranges omit the offset, the restarted one is explicit
    assert byterange_lines(content) == ["9400@376", "7144", "8024", "10340@47000"]
    assert [line for line in content.splitlines() if line == "segment1.ts"] == [
        "segment1.ts"
    ] * 4

    # cumulative durations are preserved
    assert extinf_durations(content) == [4.12, 3.56, 3.82, 4.0]
    assert sum(extinf_durations(content)) == pytest.approx(15.5)


def test_build_iframe_playlist_with_two_uris():
    playlist = build_iframe_playlist(DUAL_URI_KEYFRAMES)
    content = playlist.dumps()

    # offset is always explicit right after a URI switch, omittable only
    # inside a contiguous run of the same URI
    assert byterange_lines(content) == [
        "9400@376",
        "10340@1880",
        "6600",
        "7144@9776",
    ]
    assert [seg.uri for seg in playlist.segments] == [
        "segment1.ts",
        "segment2.ts",
        "segment2.ts",
        "segment1.ts",
    ]
    assert sum(extinf_durations(content)) == pytest.approx(14.0)
    assert "#EXT-X-TARGETDURATION:5\n" in content


def test_build_iframe_playlist_serializes_media_sequence():
    playlist = build_iframe_playlist(MULTI_SEGMENT_SAME_URI_KEYFRAMES, media_sequence=7)
    content = playlist.dumps()

    assert "#EXT-X-MEDIA-SEQUENCE:7\n" in content

    parsed = m3u8.loads(content)
    assert parsed.media_sequence == 7
    assert [seg.media_sequence for seg in parsed.segments] == [7, 8, 9, 10]


def test_build_iframe_playlist_round_trips_back_to_same_records():
    for keyframes in (MULTI_SEGMENT_SAME_URI_KEYFRAMES, DUAL_URI_KEYFRAMES):
        parsed = m3u8.loads(build_iframe_playlist(keyframes).dumps())

        assert parsed.is_i_frames_only
        assert parsed.is_endlist
        assert parsed.version == 4
        assert keyframes_from_playlist(parsed) == keyframes


def test_build_iframe_playlist_rejects_empty_keyframe_list():
    with pytest.raises(ValueError, match="empty keyframe list"):
        build_iframe_playlist([])


def test_build_iframe_playlist_rejects_non_positive_duration():
    for duration in (0, -1.5):
        keyframes = [
            KeyFrame(uri="segment1.ts", byte_offset=0, byte_length=100, duration=2.0),
            KeyFrame(
                uri="segment1.ts", byte_offset=100, byte_length=100, duration=duration
            ),
        ]
        with pytest.raises(ValueError, match="non-positive duration"):
            build_iframe_playlist(keyframes)


def test_build_iframe_playlist_rejects_out_of_bounds_ranges():
    base = dict(uri="segment1.ts", byte_length=100, duration=2.0)
    with pytest.raises(ValueError, match="out of bounds byte offset"):
        build_iframe_playlist([KeyFrame(byte_offset=-1, **base)])
    with pytest.raises(ValueError, match="out of bounds byte length"):
        build_iframe_playlist(
            [KeyFrame(byte_offset=0, byte_length=0, duration=2.0, uri="segment1.ts")]
        )


def test_build_iframe_playlist_rejects_overlapping_ranges():
    keyframes = [
        KeyFrame(uri="segment1.ts", byte_offset=0, byte_length=1000, duration=2.0),
        KeyFrame(uri="segment1.ts", byte_offset=500, byte_length=1000, duration=2.0),
    ]
    with pytest.raises(ValueError, match="overlaps"):
        build_iframe_playlist(keyframes)

    # overlap across different files is not an error
    keyframes = [
        KeyFrame(uri="segment1.ts", byte_offset=0, byte_length=1000, duration=2.0),
        KeyFrame(uri="segment2.ts", byte_offset=500, byte_length=1000, duration=2.0),
    ]
    assert len(build_iframe_playlist(keyframes).segments) == 2


def test_build_iframe_playlist_rejects_backwards_timestamps():
    keyframes = [
        KeyFrame(
            uri="segment1.ts",
            byte_offset=0,
            byte_length=100,
            duration=2.0,
            timestamp=10.0,
        ),
        KeyFrame(
            uri="segment1.ts",
            byte_offset=100,
            byte_length=100,
            duration=2.0,
            timestamp=9.0,
        ),
    ]
    with pytest.raises(ValueError, match="goes backwards"):
        build_iframe_playlist(keyframes)

    # non-decreasing timestamps are accepted
    keyframes[1] = keyframes[1]._replace(timestamp=10.0)
    assert len(build_iframe_playlist(keyframes).segments) == 2
