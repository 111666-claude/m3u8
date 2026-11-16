# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

# Tests for applying a key rotation schedule (three AES-128 rotations
# followed by a clear stretch) to a Media Playlist.

import playlists
import pytest

import m3u8
from m3u8 import (
    KeyInterval,
    KeyScheduleError,
    apply_key_schedule,
    key_intervals_from_playlist,
)

IV0 = "0x000102030405060708090a0b0c0d0e0f"
IV1 = "0x101112131415161718191a1b1c1d1e1f"
IV2 = "0x202122232425262728292a2b2c2d2e2f"


def rotated_intervals():
    return [
        KeyInterval(100, 102, uri="keys/key0.bin", iv=IV0),
        KeyInterval(102, 104, uri="keys/key1.bin", iv=IV1),
        KeyInterval(104, 106, uri="keys/key2.bin", iv=IV2),
        KeyInterval(106, 107, method="NONE"),
    ]


def key_lines(content):
    return [line for line in content.splitlines() if line.startswith("#EXT-X-KEY")]


def test_apply_three_rotations_and_clear_stretch_dumps_expected_tags():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)

    apply_key_schedule(playlist, rotated_intervals())

    assert playlist.dumps().strip() == playlists.KEY_SCHEDULE_ROTATED_PLAYLIST.strip()
    assert key_lines(playlist.dumps()) == [
        f'#EXT-X-KEY:METHOD=AES-128,URI="keys/key0.bin",IV={IV0}',
        f'#EXT-X-KEY:METHOD=AES-128,URI="keys/key1.bin",IV={IV1}',
        f'#EXT-X-KEY:METHOD=AES-128,URI="keys/key2.bin",IV={IV2}',
        "#EXT-X-KEY:METHOD=NONE",
    ]


def test_apply_schedule_associates_each_segment_with_its_key_and_iv():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)

    playlist.apply_key_schedule(rotated_intervals())

    assert [segment.key.method for segment in playlist.segments] == [
        "AES-128",
        "AES-128",
        "AES-128",
        "AES-128",
        "AES-128",
        "AES-128",
        "NONE",
    ]
    assert [
        segment.key.uri if segment.key else None for segment in playlist.segments
    ] == [
        "keys/key0.bin",
        "keys/key0.bin",
        "keys/key1.bin",
        "keys/key1.bin",
        "keys/key2.bin",
        "keys/key2.bin",
        None,
    ]
    assert [
        segment.key.iv if segment.key else None for segment in playlist.segments
    ] == [
        IV0,
        IV0,
        IV1,
        IV1,
        IV2,
        IV2,
        None,
    ]
    # One Key object is shared by every segment of an interval, which is
    # what keeps a single EXT-X-KEY tag at each switch point.
    assert playlist.segments[0].key is playlist.segments[1].key
    assert playlist.segments[2].key is playlist.segments[3].key


def test_apply_schedule_preserves_uri_duration_byterange_and_media_sequence():
    original = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)

    playlist.apply_key_schedule(rotated_intervals())

    assert playlist.media_sequence == original.media_sequence == 100
    assert playlist.segments.uri == original.segments.uri
    assert [segment.duration for segment in playlist.segments] == [
        segment.duration for segment in original.segments
    ]
    assert [segment.byterange for segment in playlist.segments] == [
        segment.byterange for segment in original.segments
    ]
    assert [segment.media_sequence for segment in playlist.segments] == [
        100,
        101,
        102,
        103,
        104,
        105,
        106,
    ]
    assert playlist.playlist_type == "vod"
    assert playlist.is_endlist is True


def test_apply_schedule_rebuilds_keys_and_files():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    assert playlist.keys == [None]
    assert playlist.files == playlist.segments.uri

    playlist.apply_key_schedule(rotated_intervals())

    assert [key.method for key in playlist.keys] == [
        "AES-128",
        "AES-128",
        "AES-128",
        "NONE",
    ]
    assert [key.uri for key in playlist.keys] == [
        "keys/key0.bin",
        "keys/key1.bin",
        "keys/key2.bin",
        None,
    ]
    assert playlist.files[:3] == ["keys/key0.bin", "keys/key1.bin", "keys/key2.bin"]
    assert playlist.files[3:] == playlist.segments.uri


def test_serialized_schedule_round_trips_to_the_same_intervals():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    playlist.apply_key_schedule(rotated_intervals())

    reloaded = m3u8.loads(playlist.dumps())

    assert reloaded.key_intervals() == rotated_intervals()
    assert key_intervals_from_playlist(reloaded) == rotated_intervals()
    # and the reloaded playlist serializes exactly like the first one
    assert reloaded.dumps() == playlist.dumps()


def test_schedule_can_be_reapplied_to_the_reloaded_playlist():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    playlist.apply_key_schedule(rotated_intervals())

    reloaded = m3u8.loads(playlist.dumps())
    reloaded.apply_key_schedule(reloaded.key_intervals())

    assert reloaded.dumps() == playlist.dumps()


def test_schedule_accepts_segment_indexes():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    intervals = [
        KeyInterval(0, 2, uri="keys/key0.bin", iv=IV0),
        KeyInterval(2, 4, uri="keys/key1.bin", iv=IV1),
        KeyInterval(4, 6, uri="keys/key2.bin", iv=IV2),
        KeyInterval(6, 7, method="NONE"),
    ]

    playlist.apply_key_schedule(intervals, axis="index")

    assert playlist.dumps().strip() == playlists.KEY_SCHEDULE_ROTATED_PLAYLIST.strip()
    assert playlist.key_intervals(axis="index") == intervals


def test_schedule_axis_is_detected_automatically():
    by_sequence = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    by_sequence.apply_key_schedule(rotated_intervals(), axis="auto")
    assert by_sequence.dumps().strip() == (
        playlists.KEY_SCHEDULE_ROTATED_PLAYLIST.strip()
    )

    by_index = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    intervals = [
        KeyInterval(0, 2, uri="keys/key0.bin", iv=IV0),
        KeyInterval(2, 4, uri="keys/key1.bin", iv=IV1),
        KeyInterval(4, 6, uri="keys/key2.bin", iv=IV2),
        KeyInterval(6, 7, method="NONE"),
    ]
    by_index.apply_key_schedule(intervals, axis="auto")
    assert by_index.dumps().strip() == playlists.KEY_SCHEDULE_ROTATED_PLAYLIST.strip()


def test_leading_clear_interval_emits_no_key_tag():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    intervals = [
        KeyInterval(100, 103, method="NONE"),
        KeyInterval(103, 107, uri="keys/key1.bin", iv=IV1),
    ]

    playlist.apply_key_schedule(intervals)

    assert key_lines(playlist.dumps()) == [
        f'#EXT-X-KEY:METHOD=AES-128,URI="keys/key1.bin",IV={IV1}'
    ]
    assert playlist.segments[0].key is None
    assert playlist.segments[2].key is None
    assert playlist.segments[3].key.uri == "keys/key1.bin"
    assert playlist.key_intervals() == intervals


def test_clear_only_schedule_leaves_the_playlist_unchanged():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    expected = playlist.dumps()

    playlist.apply_key_schedule([KeyInterval(100, 107, method="NONE")])

    assert playlist.dumps() == expected
    assert playlist.keys == [None]
    assert all(segment.key is None for segment in playlist.segments)


def test_adjacent_identical_intervals_are_merged():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    intervals = [
        KeyInterval(100, 101, uri="keys/key0.bin", iv=IV0),
        KeyInterval(101, 102, uri="keys/key0.bin", iv=IV0),
        KeyInterval(102, 107, method="NONE"),
    ]

    playlist.apply_key_schedule(intervals)

    assert key_lines(playlist.dumps()) == [
        f'#EXT-X-KEY:METHOD=AES-128,URI="keys/key0.bin",IV={IV0}',
        "#EXT-X-KEY:METHOD=NONE",
    ]
    assert playlist.key_intervals() == [
        KeyInterval(100, 102, uri="keys/key0.bin", iv=IV0),
        KeyInterval(102, 107, method="NONE"),
    ]


def test_schedule_accepts_mappings_and_sequences():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    intervals = [
        {"start": 100, "end": 102, "uri": "keys/key0.bin", "iv": IV0},
        (102, 104, "AES-128", "keys/key1.bin", IV1),
        KeyInterval(104, 106, uri="keys/key2.bin", iv=IV2),
        {"start": 106, "end": 107, "method": "NONE"},
    ]

    playlist.apply_key_schedule(intervals)

    assert playlist.dumps().strip() == playlists.KEY_SCHEDULE_ROTATED_PLAYLIST.strip()


def test_schedule_accepts_lowercase_method_and_ivs_without_iv():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    playlist.apply_key_schedule(
        [
            KeyInterval(100, 104, method="aes-128", uri="keys/key0.bin"),
            KeyInterval(104, 107, method="none"),
        ]
    )

    assert key_lines(playlist.dumps()) == [
        '#EXT-X-KEY:METHOD=AES-128,URI="keys/key0.bin"',
        "#EXT-X-KEY:METHOD=NONE",
    ]


def test_schedule_replaces_existing_keys_of_an_encrypted_playlist():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_ROTATED_PLAYLIST)

    playlist.apply_key_schedule([KeyInterval(100, 107, method="NONE")])

    assert key_lines(playlist.dumps()) == []
    assert playlist.keys == [None]
    assert playlist.files == playlist.segments.uri


def test_schedule_is_applied_before_serialization_only_once():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)

    playlist.apply_key_schedule(rotated_intervals())
    first = playlist.dumps()
    playlist.apply_key_schedule(rotated_intervals())

    assert playlist.dumps() == first
    assert len(playlist.keys) == 4


def _assert_rejected(intervals, match, axis="auto", source=None):
    playlist = m3u8.loads(source or playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    expected = playlist.dumps()
    with pytest.raises(KeyScheduleError, match=match):
        playlist.apply_key_schedule(intervals, axis=axis)
    # a rejected schedule never leaves a half-applied playlist behind
    assert playlist.dumps() == expected


def test_schedule_rejects_overlapping_intervals():
    _assert_rejected(
        [
            KeyInterval(100, 104, uri="keys/key0.bin", iv=IV0),
            KeyInterval(102, 107, uri="keys/key1.bin", iv=IV1),
        ],
        "overlap",
    )


def test_schedule_rejects_gaps_between_intervals():
    _assert_rejected(
        [
            KeyInterval(100, 102, uri="keys/key0.bin", iv=IV0),
            KeyInterval(103, 107, uri="keys/key1.bin", iv=IV1),
        ],
        "gap",
    )


def test_schedule_rejects_incomplete_coverage():
    _assert_rejected(
        [KeyInterval(100, 106, uri="keys/key0.bin", iv=IV0)],
        "gap at the end",
    )


def test_schedule_rejects_out_of_bounds_intervals():
    _assert_rejected(
        [KeyInterval(99, 107, uri="keys/key0.bin", iv=IV0)],
        "out of bounds",
    )
    _assert_rejected(
        [KeyInterval(100, 108, uri="keys/key0.bin", iv=IV0)],
        "out of bounds",
    )
    _assert_rejected(
        [KeyInterval(0, 7, uri="keys/key0.bin", iv=IV0)],
        "out of bounds",
        axis="media_sequence",
    )


def test_schedule_rejects_index_axis_out_of_bounds():
    _assert_rejected(
        [KeyInterval(0, 8, uri="keys/key0.bin", iv=IV0)],
        "out of bounds",
        axis="index",
    )


def test_schedule_rejects_out_of_order_intervals():
    _assert_rejected(
        [
            KeyInterval(102, 107, uri="keys/key1.bin", iv=IV1),
            KeyInterval(100, 102, uri="keys/key0.bin", iv=IV0),
        ],
        "out of order",
    )


def test_schedule_rejects_invalid_ivs():
    for iv in (
        "not-an-iv",
        "0x1234",
        "0x" + "z" * 32,
        "00" * 16,
        "0x" + "a" * 31,
        "0x" + "a" * 33,
        0xDEADBEEF,
    ):
        _assert_rejected(
            [
                KeyInterval(100, 102, uri="keys/key0.bin", iv=iv),
                KeyInterval(102, 107, method="NONE"),
            ],
            "invalid IV",
        )


def test_schedule_rejects_missing_key_uri():
    for uri in (None, "", "   ", 123):
        _assert_rejected(
            [
                KeyInterval(100, 102, uri=uri),
                KeyInterval(102, 107, method="NONE"),
            ],
            "requires a non-empty key URI",
        )


def test_schedule_rejects_unsupported_methods():
    _assert_rejected(
        [
            KeyInterval(100, 102, method="SAMPLE-AES", uri="keys/key0.bin"),
            KeyInterval(102, 107, method="NONE"),
        ],
        "unsupported method",
    )
    _assert_rejected(
        [
            KeyInterval(100, 102, method="AES-256", uri="keys/key0.bin"),
            KeyInterval(102, 107, method="NONE"),
        ],
        "unsupported method",
    )


def test_schedule_rejects_uri_or_iv_on_clear_intervals():
    _assert_rejected(
        [
            KeyInterval(100, 102, uri="keys/key0.bin", iv=IV0),
            KeyInterval(102, 107, method="NONE", uri="keys/key0.bin"),
        ],
        "must not carry a key URI",
    )
    _assert_rejected(
        [
            KeyInterval(100, 102, uri="keys/key0.bin", iv=IV0),
            KeyInterval(102, 107, method="NONE", iv=IV0),
        ],
        "must not carry an IV",
    )


def test_schedule_rejects_empty_or_reversed_intervals():
    _assert_rejected(
        [KeyInterval(100, 107, uri="k.bin", iv=IV0), KeyInterval(107, 107)],
        "empty or reversed",
    )
    _assert_rejected(
        [KeyInterval(105, 100, uri="k.bin", iv=IV0)],
        "empty or reversed",
    )


def test_schedule_rejects_non_integer_bounds():
    _assert_rejected(
        [
            KeyInterval(100.5, 107, uri="keys/key0.bin", iv=IV0),
        ],
        "non-integer bounds",
    )


def test_schedule_rejects_an_empty_schedule():
    _assert_rejected([], "at least one interval")


def test_schedule_rejects_non_contiguous_media_sequence_numbers():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    playlist.segments[3].media_sequence = 500

    with pytest.raises(KeyScheduleError, match="non-contiguous Media Sequence"):
        apply_key_schedule(playlist, rotated_intervals())

    # segment indexes remain unambiguous
    apply_key_schedule(
        playlist,
        [
            KeyInterval(0, 2, uri="keys/key0.bin", iv=IV0),
            KeyInterval(2, 4, uri="keys/key1.bin", iv=IV1),
            KeyInterval(4, 6, uri="keys/key2.bin", iv=IV2),
            KeyInterval(6, 7, method="NONE"),
        ],
        axis="index",
    )
    assert playlist.segments[5].key.uri == "keys/key2.bin"


def test_schedule_rejects_unknown_axis():
    _assert_rejected(
        [KeyInterval(100, 107, uri="keys/key0.bin", iv=IV0)],
        "unknown key interval axis",
        axis="frames",
    )


def test_schedule_rejects_variant_playlists():
    variant = m3u8.loads(playlists.VARIANT_PLAYLIST)

    with pytest.raises(KeyScheduleError, match="Media Playlist"):
        apply_key_schedule(variant, [KeyInterval(0, 1, uri="k.bin", iv=IV0)])
    with pytest.raises(KeyScheduleError, match="Media Playlist"):
        key_intervals_from_playlist(variant)


def test_schedule_rejects_playlists_without_segments():
    with pytest.raises(KeyScheduleError, match="playlist with segments"):
        apply_key_schedule(m3u8.M3U8("#EXTM3U\n"), [KeyInterval(0, 1)])


def test_key_intervals_rejects_playlists_without_segments():
    with pytest.raises(KeyScheduleError, match="playlist with segments"):
        key_intervals_from_playlist(m3u8.M3U8("#EXTM3U\n"))


def test_key_intervals_returns_clear_stretches_as_method_none():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)

    intervals = playlist.key_intervals()

    assert intervals == [KeyInterval(100, 107, method="NONE")]
    assert intervals[0].uri is None
    assert intervals[0].iv is None


def test_key_intervals_with_index_axis_use_zero_based_indexes():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_ROTATED_PLAYLIST)

    intervals = playlist.key_intervals(axis="index")

    assert intervals == [
        KeyInterval(0, 2, uri="keys/key0.bin", iv=IV0),
        KeyInterval(2, 4, uri="keys/key1.bin", iv=IV1),
        KeyInterval(4, 6, uri="keys/key2.bin", iv=IV2),
        KeyInterval(6, 7, method="NONE"),
    ]


def test_key_intervals_preserve_no_iv_keys_and_relative_uris():
    playlist = m3u8.loads(
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:9\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:4,\na.ts\n'
    )

    assert playlist.key_intervals() == [KeyInterval(9, 10, uri="key.bin", iv=None)]


def test_key_schedule_error_is_a_value_error():
    assert issubclass(KeyScheduleError, ValueError)


def test_schedule_is_applied_through_the_base_path():
    playlist = m3u8.loads(
        playlists.KEY_SCHEDULE_CLEAR_PLAYLIST,
        uri="http://videoserver.com/hls/replay.m3u8",
    )
    playlist.base_path = "http://cdn.example.com/keys"

    playlist.apply_key_schedule(rotated_intervals())

    assert key_lines(playlist.dumps())[:3] == [
        f'#EXT-X-KEY:METHOD=AES-128,URI="http://cdn.example.com/keys/key0.bin",IV={IV0}',
        f'#EXT-X-KEY:METHOD=AES-128,URI="http://cdn.example.com/keys/key1.bin",IV={IV1}',
        f'#EXT-X-KEY:METHOD=AES-128,URI="http://cdn.example.com/keys/key2.bin",IV={IV2}',
    ]


def test_schedule_keeps_base_uri_for_resolving_key_uris():
    playlist = m3u8.loads(
        playlists.KEY_SCHEDULE_CLEAR_PLAYLIST,
        uri="http://videoserver.com/hls/replay.m3u8",
    )

    playlist.apply_key_schedule(rotated_intervals())

    assert playlist.segments[0].key.base_uri == "http://videoserver.com/hls/"
    assert (
        playlist.segments[0].key.absolute_uri
        == "http://videoserver.com/hls/keys/key0.bin"
    )


def test_method_none_with_empty_uri_round_trips_as_method_none():
    playlist = m3u8.loads(playlists.KEY_SCHEDULE_CLEAR_PLAYLIST)
    playlist.apply_key_schedule(rotated_intervals())

    # an existing playlist may spell the clear switch as METHOD=NONE,URI=""
    reloaded = m3u8.loads(
        playlist.dumps().replace(
            "#EXT-X-KEY:METHOD=NONE", '#EXT-X-KEY:METHOD=NONE,URI=""'
        )
    )

    assert reloaded.key_intervals() == rotated_intervals()
