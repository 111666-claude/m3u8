# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import playlists
import pytest

import m3u8
from m3u8 import (
    InvalidStartOffsetError,
    MissingDurationError,
    MissingStartTagError,
    NoPlayableSegmentsError,
    resolve_start,
)
from m3u8.model import Segment


def test_positive_offset_lands_inside_segment():
    playlist = m3u8.loads(playlists.START_POSITION_VOD_PLAYLIST)
    result = resolve_start(playlist)

    assert result.segment is playlist.segments[2]
    assert result.segment_index == 2
    assert result.offset == 5.0
    assert result.position == 25.0
    assert result.time_offset == 25.0
    assert result.precise is True
    assert result.clamped is None
    assert result.part is None
    assert result.skipped_gaps == 0


def test_positive_offset_crosses_discontinuity():
    playlist = m3u8.loads(playlists.START_POSITION_VOD_PLAYLIST)
    result = resolve_start(playlist)

    assert result.segment.discontinuity is True
    assert result.discontinuities_crossed == 1


def test_negative_offset_is_measured_from_playlist_end():
    playlist = m3u8.loads(playlists.START_POSITION_VOD_PLAYLIST)
    result = resolve_start(playlist, time_offset=-15.0)

    assert result.segment_index == 2
    assert result.offset == 5.0
    assert result.position == 25.0


def test_negative_offset_on_existing_fixture():
    playlist = m3u8.loads(playlists.SIMPLE_PLAYLIST_WITH_START_NEGATIVE_OFFSET)
    result = resolve_start(playlist)

    assert result.segment_index == 0
    assert result.offset == 5218.0
    assert result.position == 5218.0


def test_precise_yes_on_existing_fixture():
    playlist = m3u8.loads(playlists.SIMPLE_PLAYLIST_WITH_START_PRECISE)
    result = resolve_start(playlist)

    assert result.precise is True
    assert result.offset == 10.5


def test_precise_defaults_to_false_and_can_be_overridden():
    playlist = m3u8.loads(playlists.START_POSITION_GAP_PLAYLIST)

    assert resolve_start(playlist).precise is False
    assert resolve_start(playlist, precise="YES").precise is True
    assert resolve_start(playlist, precise="NO").precise is False


def test_live_window_negative_offset_from_live_edge():
    playlist = m3u8.loads(playlists.START_POSITION_LIVE_PLAYLIST)
    assert playlist.is_endlist is False

    result = resolve_start(playlist)

    # total = 3 * 4.0 + 3 * 1.0 = 15.0; 15.0 - 6.0 = 9.0
    assert result.segment is playlist.segments[2]
    assert result.offset == 1.0
    assert result.position == 9.0
    assert result.precise is False
    assert result.clamped is None


def test_live_window_lands_in_partial_segment_parts():
    playlist = m3u8.loads(playlists.START_POSITION_LIVE_PLAYLIST)
    result = resolve_start(playlist, time_offset=-2.5)

    # position 12.5: inside the partial segment (starts at 12.0)
    segment = playlist.segments[3]
    assert segment.uri is None
    assert result.segment is segment
    assert result.offset == 0.5
    assert result.part is segment.parts[0]
    assert result.part_index == 0
    assert result.part_offset == 0.5


def test_partial_segment_part_boundary_belongs_to_next_part():
    playlist = m3u8.loads(playlists.START_POSITION_LIVE_PLAYLIST)
    result = resolve_start(playlist, time_offset=-2.0)

    assert result.segment_index == 3
    assert result.offset == 1.0
    assert result.part_index == 1
    assert result.part_offset == 0.0


def test_low_latency_fixture_resolves_to_part():
    playlist = m3u8.loads(playlists.LOW_LATENCY_PART_PLAYLIST)
    result = resolve_start(playlist, time_offset=-0.5)

    # total = 9 * 4.00008 + 3 * 0.33334 = 37.00074
    segment = playlist.segments[9]
    assert segment.uri is None
    assert result.segment is segment
    assert result.offset == pytest.approx(0.50002)
    assert result.part is segment.parts[1]
    assert result.part_index == 1
    assert result.part_offset == pytest.approx(0.16668)
    assert result.position == pytest.approx(36.50074)


def test_gap_part_is_skipped_within_partial_segment():
    playlist = m3u8.loads(playlists.START_POSITION_LIVE_GAP_PART_PLAYLIST)
    result = resolve_start(playlist)

    # total = 4.0 + 3.0 = 7.0; position 5.5 -> part 1 (GAP) -> part 2
    segment = playlist.segments[1]
    assert result.segment is segment
    assert result.part is segment.parts[2]
    assert result.part_index == 2
    assert result.part_offset == 0.0


def test_exact_boundary_belongs_to_segment_starting_there():
    playlist = m3u8.loads(playlists.START_POSITION_VOD_PLAYLIST)
    result = resolve_start(playlist, time_offset=20.0)

    assert result.segment_index == 2
    assert result.offset == 0.0
    assert result.position == 20.0
    assert result.at_boundary is True
    assert result.clamped is None


def test_zero_offset_lands_on_first_segment():
    playlist = m3u8.loads(playlists.START_POSITION_VOD_PLAYLIST)
    result = resolve_start(playlist, time_offset=0.0)

    assert result.segment_index == 0
    assert result.offset == 0.0
    assert result.at_boundary is True


def test_offset_beyond_end_is_clamped_to_last_segment():
    playlist = m3u8.loads(playlists.START_POSITION_VOD_PLAYLIST)
    result = resolve_start(playlist, time_offset=100.0)

    assert result.clamped == "end"
    assert result.segment_index == 3
    assert result.offset == 10.0
    assert result.position == 40.0


def test_negative_offset_beyond_start_is_clamped_to_first_segment():
    playlist = m3u8.loads(playlists.START_POSITION_VOD_PLAYLIST)
    result = resolve_start(playlist, time_offset=-100.0)

    assert result.clamped == "start"
    assert result.segment_index == 0
    assert result.offset == 0.0
    assert result.position == 0.0


def test_gap_segment_is_skipped_forward():
    playlist = m3u8.loads(playlists.START_POSITION_GAP_PLAYLIST)
    result = resolve_start(playlist)

    # offset 15.0 lands inside seg1 (GAP): move to seg2
    assert result.segment is playlist.segments[2]
    assert result.offset == 0.0
    assert result.position == 20.0
    assert result.skipped_gaps == 1


def test_all_gap_playlist_has_no_playable_segments():
    playlist = m3u8.loads(playlists.START_POSITION_ALL_GAP_PLAYLIST)

    with pytest.raises(NoPlayableSegmentsError):
        resolve_start(playlist)


def test_playlist_without_segments_has_no_playable_segments():
    playlist = m3u8.loads("#EXTM3U\n#EXT-X-START:TIME-OFFSET=1.0\n#EXT-X-ENDLIST\n")

    with pytest.raises(NoPlayableSegmentsError):
        resolve_start(playlist)


def test_missing_duration_raises():
    playlist = m3u8.M3U8()
    playlist.add_segment(Segment(uri="a.ts", duration=None))
    playlist.add_segment(Segment(uri="b.ts", duration=1.0))

    with pytest.raises(MissingDurationError):
        resolve_start(playlist, time_offset=0.5)


@pytest.mark.parametrize(
    "bad_offset", [float("nan"), float("inf"), -float("inf"), "abc"]
)
def test_invalid_offset_raises(bad_offset):
    playlist = m3u8.loads(playlists.START_POSITION_VOD_PLAYLIST)

    with pytest.raises(InvalidStartOffsetError):
        resolve_start(playlist, time_offset=bad_offset)


def test_missing_start_tag_raises_without_override():
    playlist = m3u8.loads(playlists.SIMPLE_PLAYLIST)
    assert playlist.start is None

    with pytest.raises(MissingStartTagError):
        resolve_start(playlist)


def test_offset_override_works_without_start_tag():
    playlist = m3u8.loads(playlists.SIMPLE_PLAYLIST)
    result = resolve_start(playlist, time_offset=0.0)

    assert result.segment_index == 0
    assert result.precise is False


def test_resolution_does_not_mutate_playlist():
    playlist = m3u8.loads(playlists.START_POSITION_LIVE_PLAYLIST)
    before = playlist.dumps()

    resolve_start(playlist)
    resolve_start(playlist, time_offset=-2.5, precise=True)

    assert playlist.dumps() == before
    assert playlist.start.time_offset == -6.0
    assert playlist.start.precise == "NO"


def test_m3u8_resolve_start_method():
    playlist = m3u8.loads(playlists.START_POSITION_VOD_PLAYLIST)
    result = playlist.resolve_start()

    assert result.segment_index == 2
    assert result.offset == 5.0
