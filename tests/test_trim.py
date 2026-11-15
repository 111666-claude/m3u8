# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

# Tests for M3U8.trim(): cutting a VOD media playlist by playback time.

import datetime

import playlists
import pytest

import m3u8


def _trimmed(start, end):
    playlist = m3u8.loads(playlists.TRIM_VOD_PLAYLIST)
    return playlist.trim(start, end)


def test_trim_selects_minimal_window_of_whole_segments():
    result = _trimmed(12, 35)

    assert [segment.uri for segment in result.playlist.segments] == [
        "seg41.ts",
        "seg42.ts",
        "seg43.ts",
    ]
    # actual window covers whole segments containing [12, 35)
    assert result.start == 10.0
    assert result.end == 39.75


def test_trim_returns_named_tuple_with_actual_window():
    result = _trimmed(12, 35)

    assert result.playlist is not None
    assert (result.start, result.end) == (10.0, 39.75)


def test_trim_exact_segment_boundaries():
    result = _trimmed(10, 18.5)

    assert [segment.uri for segment in result.playlist.segments] == ["seg41.ts"]
    assert result.start == 10.0
    assert result.end == 18.5


def test_trim_clamps_interval_to_playlist_bounds():
    result = _trimmed(-5, 12)
    assert result.playlist.segments[0].uri == "seg40.ts"
    assert result.start == 0.0

    result = _trimmed(49, 1000)
    assert result.playlist.segments[-1].uri == "seg44.ts"
    assert result.end == 50.75


def test_trim_first_segment_inherits_effective_key_map_and_program_date_time():
    result = _trimmed(12, 35)
    output = result.playlist.dumps()

    # seg41 has no EXT-X-KEY of its own in the source playlist; the
    # trimmed playlist must repeat the key in effect at that point
    assert output.count("#EXT-X-KEY") == 2
    assert (
        '#EXT-X-KEY:METHOD=AES-128,URI="key1.bin",'
        "IV=0x00000000000000000000000000000001" in output
    )
    # key rotation inside the window is preserved
    assert (
        '#EXT-X-KEY:METHOD=AES-128,URI="key2.bin",'
        "IV=0x00000000000000000000000000000002" in output
    )
    # initialization map is emitted before the first segment
    assert '#EXT-X-MAP:URI="init.mp4"' in output
    # program date time is anchored explicitly on the first segment
    assert "#EXT-X-PROGRAM-DATE-TIME:2024-01-01T00:00:10.000+00:00" in output


def test_trim_first_segment_key_and_map_objects():
    result = _trimmed(12, 35)
    first = result.playlist.segments[0]

    assert first.key is not None
    assert first.key.uri == "key1.bin"
    assert first.init_section is not None
    assert first.init_section.uri == "init.mp4"
    assert first.program_date_time == datetime.datetime(
        2024, 1, 1, 0, 0, 10, tzinfo=datetime.timezone.utc
    )


def test_trim_adapts_media_and_discontinuity_sequences():
    result = _trimmed(12, 35)

    assert result.playlist.media_sequence == 41
    assert result.playlist.discontinuity_sequence == 2
    assert [segment.media_sequence for segment in result.playlist.segments] == [
        41,
        42,
        43,
    ]
    # the discontinuity inside the window is kept
    assert result.playlist.segments[-1].discontinuity is True
    assert "#EXT-X-DISCONTINUITY\n" in result.playlist.dumps()


def test_trim_folds_leading_discontinuity_into_sequence():
    result = _trimmed(31, 50)

    assert [segment.uri for segment in result.playlist.segments] == [
        "seg43.ts",
        "seg44.ts",
    ]
    # the discontinuity before seg43 becomes part of the sequence number
    assert result.playlist.discontinuity_sequence == 3
    assert result.playlist.media_sequence == 43
    assert "#EXT-X-DISCONTINUITY\n" not in result.playlist.dumps()


def test_trim_keeps_only_dateranges_inside_the_window():
    result = _trimmed(12, 35)
    output = result.playlist.dumps()

    assert 'ID="ad-2"' in output
    assert 'ID="ad-1"' not in output


def test_trim_output_is_a_complete_vod_playlist():
    result = _trimmed(12, 35)
    output = result.playlist.dumps()

    assert output.startswith("#EXTM3U")
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in output
    assert "#EXT-X-MEDIA-SEQUENCE:41" in output
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:2" in output
    assert output.rstrip().endswith("#EXT-X-ENDLIST")


def test_trim_does_not_modify_original_playlist():
    playlist = m3u8.loads(playlists.TRIM_VOD_PLAYLIST)
    dumps_before = playlist.dumps()

    playlist.trim(12, 35)

    assert playlist.dumps() == dumps_before
    assert len(playlist.segments) == 5
    assert playlist.media_sequence == 40
    assert playlist.segments[0].media_sequence == 40
    assert playlist.segments[1].program_date_time is None
    assert playlist.segments[3].discontinuity is True


def test_trim_result_survives_dumps_loads_roundtrip():
    original = m3u8.loads(playlists.TRIM_VOD_PLAYLIST)
    result = original.trim(12, 35)

    reloaded = m3u8.loads(result.playlist.dumps())

    assert [segment.uri for segment in reloaded.segments] == [
        "seg41.ts",
        "seg42.ts",
        "seg43.ts",
    ]
    assert reloaded.media_sequence == 41
    assert reloaded.discontinuity_sequence == 2
    assert reloaded.is_endlist is True
    assert reloaded.playlist_type == "vod"
    assert reloaded.segments[0].key.uri == "key1.bin"
    assert reloaded.segments[1].key.uri == "key2.bin"
    assert reloaded.segments[2].key.uri == "key2.bin"
    assert reloaded.segments[0].init_section.uri == "init.mp4"
    assert reloaded.segments[0].program_date_time == datetime.datetime(
        2024, 1, 1, 0, 0, 10, tzinfo=datetime.timezone.utc
    )
    assert reloaded.segments[2].discontinuity is True
    assert [dr.id for dr in reloaded.segments[2].dateranges] == ["ad-2"]
    # serializing the reloaded playlist is stable
    assert m3u8.loads(reloaded.dumps()).dumps() == reloaded.dumps()


def test_trim_keys_and_files_of_trimmed_playlist():
    result = _trimmed(12, 35)

    assert [key.uri for key in result.playlist.keys] == ["key1.bin", "key2.bin"]
    assert result.playlist.files == [
        "key1.bin",
        "key2.bin",
        "seg41.ts",
        "seg42.ts",
        "seg43.ts",
    ]


def test_trim_rejects_empty_interval():
    playlist = m3u8.loads(playlists.TRIM_VOD_PLAYLIST)

    with pytest.raises(ValueError):
        playlist.trim(5, 5)
    with pytest.raises(ValueError):
        playlist.trim(6, 5)


def test_trim_rejects_interval_outside_playlist():
    playlist = m3u8.loads(playlists.TRIM_VOD_PLAYLIST)

    with pytest.raises(ValueError):
        playlist.trim(60, 70)
    with pytest.raises(ValueError):
        playlist.trim(-100, -50)


def test_trim_rejects_non_finite_bounds():
    playlist = m3u8.loads(playlists.TRIM_VOD_PLAYLIST)

    with pytest.raises(ValueError):
        playlist.trim(float("nan"), 10)
    with pytest.raises(ValueError):
        playlist.trim(0, float("inf"))
    with pytest.raises(ValueError):
        playlist.trim(float("-inf"), 10)
    with pytest.raises(ValueError):
        playlist.trim("not-a-number", 10)


def test_trim_rejects_live_playlist_without_endlist():
    playlist = m3u8.loads(playlists.SLIDING_WINDOW_PLAYLIST)
    assert not playlist.is_endlist

    with pytest.raises(ValueError):
        playlist.trim(0, 5)


def test_trim_rejects_variant_playlist():
    playlist = m3u8.loads(playlists.VARIANT_PLAYLIST)
    assert playlist.is_variant

    with pytest.raises(ValueError):
        playlist.trim(0, 5)


def test_trim_rejects_playlist_with_partial_segments():
    content = playlists.LOW_LATENCY_PART_PLAYLIST + "#EXT-X-ENDLIST\n"
    playlist = m3u8.loads(content)
    assert playlist.is_endlist

    with pytest.raises(ValueError):
        playlist.trim(0, 5)
