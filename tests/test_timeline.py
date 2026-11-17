# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import datetime

import playlists
import pytest

import m3u8
from m3u8 import (
    SegmentTimeline,
    TimelineEntry,
    TimelineError,
    TimelineSpan,
    build_segment_timeline,
)

utc = datetime.timezone.utc


def test_gap_timeline_entries_follow_media_sequence():
    timeline = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST).gap_timeline()

    assert isinstance(timeline, SegmentTimeline)
    assert timeline.media_sequences == (100, 101, 102, 103, 104, 105)
    assert [entry.uri for entry in timeline] == [
        "segment100.ts",
        "segment101.ts",
        "segment102.ts",
        "segment103.ts",
        "segment104.ts",
        "segment105.ts",
    ]
    # gaps occupy their EXTINF duration but are not downloadable
    assert [entry.downloadable for entry in timeline] == [
        True,
        False,
        True,
        True,
        False,
        True,
    ]
    assert [entry.gap for entry in timeline] == [
        False,
        True,
        False,
        False,
        True,
        False,
    ]


def test_gap_timeline_offsets_keep_gap_durations():
    timeline = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST).gap_timeline()

    starts = [entry.start for entry in timeline]
    ends = [entry.end for entry in timeline]
    assert starts == pytest.approx([0, 6.006, 12.012, 15.015, 19.019, 25.025])
    assert ends == starts[1:] + [pytest.approx(31.031)]
    for entry in timeline:
        assert entry.end - entry.start == pytest.approx(entry.duration)

    assert timeline.start == 0
    assert timeline.end == pytest.approx(31.031)
    assert timeline.duration == pytest.approx(31.031)
    assert timeline.gap_duration == pytest.approx(6.006 + 6.006)
    assert timeline.downloadable_duration == pytest.approx(
        6.006 + 3.003 + 4.004 + 6.006
    )


def test_gap_timeline_gap_entry_has_timeline_position():
    timeline = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST).gap_timeline()

    first_gap = timeline.entry(101)
    assert first_gap.start == pytest.approx(6.006)
    assert first_gap.end == pytest.approx(12.012)
    assert first_gap.duration == pytest.approx(6.006)
    assert first_gap.gap is True
    assert first_gap.downloadable is False
    # the URI is still reported, so callers can log which media is missing
    assert first_gap.uri == "segment101.ts"
    # the segment that follows a gap keeps its original position
    assert timeline.entry(102).start == pytest.approx(first_gap.end)


def test_gap_timeline_keeps_original_segments():
    playlist = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST)
    original_dump = playlist.dumps()
    original_segments = list(playlist.segments)

    timeline = playlist.gap_timeline()

    assert [entry.segment for entry in timeline] == original_segments
    assert [id(entry.segment) for entry in timeline] == [
        id(segment) for segment in original_segments
    ]
    assert timeline.gap_entries[0].segment.gap_tag is True
    # building a timeline must not change the playlist or its serialization
    assert playlist.dumps() == original_dump


def test_gap_timeline_program_date_time_anchors():
    timeline = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST).gap_timeline()

    first_anchor = datetime.datetime(2021, 7, 1, 12, 0, 0, tzinfo=utc)
    second_anchor = datetime.datetime(2021, 7, 1, 14, 30, 0, tzinfo=utc)

    assert timeline[0].program_date_time == first_anchor
    assert timeline[0].start_date == first_anchor
    assert timeline[0].end_date == first_anchor + datetime.timedelta(seconds=6.006)

    # segments without their own anchor continue the wall clock, and a gap
    # keeps its date range even though it cannot be downloaded
    gap = timeline.entry(101)
    assert gap.program_date_time is None
    assert gap.start_date == first_anchor + datetime.timedelta(seconds=6.006)
    assert gap.end_date == first_anchor + datetime.timedelta(seconds=12.012)

    assert timeline.entry(102).start_date == first_anchor + datetime.timedelta(
        seconds=12.012
    )

    # the discontinuity rebases the wall clock to the new anchor
    after = timeline.entry(103)
    assert after.discontinuity is True
    assert after.program_date_time == second_anchor
    assert after.start_date == second_anchor
    assert after.start_date > timeline.entry(102).end_date

    # anchors are echoed in PROGRAM-DATE-TIME order
    assert timeline.start_date == first_anchor
    assert timeline.end_date == second_anchor + datetime.timedelta(
        seconds=4.004 + 6.006 + 6.006
    )


def test_gap_timeline_discontinuity_splits_spans():
    timeline = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST).gap_timeline()

    assert len(timeline.spans) == 2
    assert all(isinstance(span, TimelineSpan) for span in timeline.spans)
    assert timeline.spans[0].media_sequences == (100, 101, 102)
    assert timeline.spans[1].media_sequences == (103, 104, 105)
    # offsets stay cumulative across the discontinuity
    assert timeline.spans[0].end == pytest.approx(timeline.spans[1].start)
    assert timeline.spans[0].duration == pytest.approx(15.015)
    assert timeline.spans[1].duration == pytest.approx(16.016)
    assert timeline.spans[0].gap_duration == pytest.approx(6.006)
    assert timeline.spans[1].gap_duration == pytest.approx(6.006)
    assert timeline.spans[0].downloadable_duration == pytest.approx(6.006 + 3.003)
    assert list(timeline.spans[0])[1] is timeline.entry(101)


def test_gap_timeline_next_downloadable_skips_gaps():
    timeline = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST).gap_timeline()

    assert timeline.next_downloadable(100).media_sequence == 102
    assert timeline.next_downloadable(102).media_sequence == 103
    assert timeline.next_downloadable(103).media_sequence == 105
    assert timeline.next_downloadable(105) is None


def test_gap_timeline_by_media_sequence_and_missing_key():
    timeline = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST).gap_timeline()

    assert timeline.by_media_sequence[104].gap is True
    with pytest.raises(KeyError):
        timeline.entry(999)


def test_gap_timeline_for_playlist_without_anchors_is_relative_only():
    timeline = m3u8.loads(playlists.GAP_PLAYLIST).gap_timeline()

    assert timeline.media_sequences == (14, 15, 16, 17)
    assert [entry.downloadable for entry in timeline] == [True, False, False, True]
    assert [entry.start for entry in timeline] == pytest.approx(
        [0, 9.84317, 18.60192, 28.48679]
    )
    assert all(entry.start_date is None for entry in timeline)
    assert all(entry.end_date is None for entry in timeline)
    assert len(timeline.spans) == 1


def test_gap_timeline_to_dicts_reports_every_segment():
    timeline = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST).gap_timeline()

    rows = timeline.to_dicts()

    assert [row["media_sequence"] for row in rows] == [100, 101, 102, 103, 104, 105]
    assert [row["uri"] for row in rows][:3] == [
        "segment100.ts",
        "segment101.ts",
        "segment102.ts",
    ]
    assert [row["downloadable"] for row in rows] == [
        True,
        False,
        True,
        True,
        False,
        True,
    ]
    gap_row = rows[1]
    assert gap_row["gap"] is True
    assert gap_row["duration"] == pytest.approx(6.006)
    assert gap_row["start"] == pytest.approx(6.006)
    assert gap_row["end"] == pytest.approx(12.012)
    assert gap_row["start_date"] == "2021-07-01T12:00:06.006000+00:00"
    assert gap_row["end_date"] == "2021-07-01T12:00:12.012000+00:00"
    # the original segment object stays available on the entry
    assert rows[1] == timeline[1].to_dict()
    assert timeline[1].segment.uri == "segment101.ts"


def test_gap_timeline_to_dicts_without_anchors_has_no_dates():
    rows = m3u8.loads(playlists.GAP_PLAYLIST).gap_timeline().to_dicts()

    assert all(row["start_date"] is None for row in rows)
    assert all(row["end_date"] is None for row in rows)
    assert [row["gap"] for row in rows] == [False, True, True, False]

    timeline = m3u8.loads(playlists.GAP_TIMELINE_MISSING_PARTS_PLAYLIST).gap_timeline()

    assert timeline.media_sequences == (0,)
    assert timeline[0].start == 0
    assert timeline[0].end == pytest.approx(6)
    assert timeline[0].gap is True
    assert timeline[0].downloadable is False


def test_timeline_rejects_playlist_without_segments():
    playlist = m3u8.loads("#EXTM3U\n#EXT-X-TARGETDURATION:6\n")

    with pytest.raises(TimelineError, match="requires a playlist with segments"):
        build_segment_timeline(playlist)


def test_timeline_rejects_master_playlist():
    playlist = m3u8.loads(playlists.VARIANT_PLAYLIST)

    with pytest.raises(TimelineError, match="Media Playlist"):
        build_segment_timeline(playlist)


def test_timeline_rejects_missing_duration():
    playlist = m3u8.loads(playlists.GAP_PLAYLIST)
    playlist.segments[0].duration = None

    with pytest.raises(TimelineError, match="no EXTINF duration"):
        build_segment_timeline(playlist)


def test_timeline_rejects_negative_duration():
    playlist = m3u8.loads(playlists.GAP_PLAYLIST)
    playlist.segments[0].duration = -1

    with pytest.raises(TimelineError, match="invalid EXTINF duration"):
        build_segment_timeline(playlist)


def test_timeline_rejects_nan_duration():
    playlist = m3u8.loads(playlists.GAP_PLAYLIST)
    playlist.segments[0].duration = float("nan")

    with pytest.raises(TimelineError, match="invalid EXTINF duration"):
        build_segment_timeline(playlist)


def test_timeline_rejects_gap_without_duration():
    playlist = m3u8.loads(playlists.GAP_PLAYLIST)
    playlist.segments[1].duration = 0

    with pytest.raises(TimelineError, match="positive EXTINF duration"):
        build_segment_timeline(playlist)


def test_timeline_rejects_duplicate_media_sequence():
    playlist = m3u8.loads(playlists.GAP_PLAYLIST)
    playlist.segments[2].media_sequence = playlist.segments[1].media_sequence

    with pytest.raises(TimelineError, match="duplicate media sequence number"):
        build_segment_timeline(playlist)


def test_timeline_rejects_skipped_media_sequence():
    playlist = m3u8.loads(playlists.GAP_PLAYLIST)
    playlist.segments[2].media_sequence += 1

    with pytest.raises(TimelineError, match="not contiguous"):
        build_segment_timeline(playlist)


def test_timeline_rejects_backwards_program_date_time():
    playlist = m3u8.loads(playlists.GAP_TIMELINE_BACKWARDS_DATE_PLAYLIST)

    with pytest.raises(TimelineError, match="goes backwards"):
        build_segment_timeline(playlist)


def test_timeline_allows_backwards_date_after_discontinuity():
    playlist = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST)
    # the anchor after the discontinuity is earlier than the gap's end date
    playlist.segments[3].program_date_time = datetime.datetime(
        2021, 7, 1, 12, 0, 10, tzinfo=utc
    )

    timeline = playlist.gap_timeline()

    assert timeline.entry(103).start_date == datetime.datetime(
        2021, 7, 1, 12, 0, 10, tzinfo=utc
    )


def test_timeline_rejects_gap_with_playable_parts():
    playlist = m3u8.loads(playlists.GAP_TIMELINE_CONTRADICTORY_PARTS_PLAYLIST)

    with pytest.raises(TimelineError, match="downloadable partial segments"):
        build_segment_timeline(playlist)


def test_timeline_accepts_gap_whose_parts_are_all_gaps():
    timeline = m3u8.loads(playlists.GAP_TIMELINE_MISSING_PARTS_PLAYLIST).gap_timeline()

    assert timeline[0].gap is True
    assert timeline[0].downloadable is False


def test_timeline_rejects_mixed_timezone_anchors():
    playlist = m3u8.loads(playlists.GAP_TIMELINE_PLAYLIST)
    playlist.segments[1].program_date_time = datetime.datetime(2021, 7, 1, 12, 0, 7)

    with pytest.raises(TimelineError, match="timezone-aware and naive"):
        build_segment_timeline(playlist)


def test_timeline_rejects_invalid_gap_tag_value():
    playlist = m3u8.loads(playlists.GAP_PLAYLIST)
    playlist.segments[0].gap_tag = "YES"

    with pytest.raises(TimelineError, match="invalid EXT-X-GAP value"):
        build_segment_timeline(playlist)


def test_timeline_is_exported_from_package():
    assert m3u8.build_segment_timeline is build_segment_timeline
    assert m3u8.TimelineEntry is TimelineEntry
    assert m3u8.TimelineSpan is TimelineSpan
    assert m3u8.SegmentTimeline is SegmentTimeline
