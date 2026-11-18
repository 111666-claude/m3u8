# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

"""
Tests for Low-Latency HLS Playlist Delta Update merging (EXT-X-SKIP).
"""

import copy

import pytest

import m3u8
from m3u8 import MergeConflictError, MergeError

# A complete Media Playlist window, with an EXT-X-KEY, an EXT-X-MAP,
# an EXT-X-DATERANGE and Partial Segments.
FULL_PLAYLIST = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-TARGETDURATION:4
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,CAN-SKIP-UNTIL=24.0
#EXT-X-PART-INF:PART-TARGET=1
#EXT-X-MEDIA-SEQUENCE:10
#EXT-X-DISCONTINUITY-SEQUENCE:1
#EXT-X-KEY:METHOD=AES-128,URI="https://priv.example.com/key.bin",IV=0X1
#EXT-X-MAP:URI="init.mp4"
#EXT-X-PROGRAM-DATE-TIME:2020-01-01T00:00:00.000Z
#EXT-X-DATERANGE:ID="ad-1",START-DATE="2020-01-01T00:00:00.000Z",DURATION=4
#EXTINF:4,
segment10.ts
#EXTINF:4,
segment11.ts
#EXT-X-PART:DURATION=1,URI="part12.0.ts",INDEPENDENT=YES
#EXT-X-PART:DURATION=1,URI="part12.1.ts"
#EXT-X-DATERANGE:ID="ad-2",START-DATE="2020-01-01T00:00:08.000Z"
#EXT-X-PART:DURATION=1,URI="part12.2.ts"
#EXTINF:4,
segment12.ts
"""

# First delta update: segment 10 is skipped, segments 11 and 12 are
# carried again and segment 13 is appended.
FIRST_DELTA = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-TARGETDURATION:4
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,CAN-SKIP-UNTIL=24.0
#EXT-X-PART-INF:PART-TARGET=1
#EXT-X-MEDIA-SEQUENCE:10
#EXT-X-DISCONTINUITY-SEQUENCE:1
#EXT-X-SKIP:SKIPPED-SEGMENTS=1
#EXT-X-KEY:METHOD=AES-128,URI="https://priv.example.com/key.bin",IV=0X1
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4,
segment11.ts
#EXT-X-PART:DURATION=1,URI="part12.0.ts",INDEPENDENT=YES
#EXT-X-PART:DURATION=1,URI="part12.1.ts"
#EXT-X-DATERANGE:ID="ad-2",START-DATE="2020-01-01T00:00:08.000Z"
#EXT-X-PART:DURATION=1,URI="part12.2.ts"
#EXTINF:4,
segment12.ts
#EXT-X-PART:DURATION=1,URI="part13.0.ts",INDEPENDENT=YES
#EXT-X-PART:DURATION=1,URI="part13.1.ts"
#EXTINF:4,
segment13.ts
#EXT-X-PRELOAD-HINT:TYPE=PART,URI="part14.0.ts"
"""

# Second delta update, built on top of the first merge result: segments
# 10 and 11 are skipped, segment 12 keeps being carried and segment 14 is
# appended.  The KEY/MAP tags are omitted to exercise inheritance across
# the skip boundary.
SECOND_DELTA = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-TARGETDURATION:4
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,CAN-SKIP-UNTIL=24.0
#EXT-X-PART-INF:PART-TARGET=1
#EXT-X-MEDIA-SEQUENCE:10
#EXT-X-DISCONTINUITY-SEQUENCE:1
#EXT-X-SKIP:SKIPPED-SEGMENTS=2
#EXT-X-PART:DURATION=1,URI="part12.0.ts",INDEPENDENT=YES
#EXT-X-PART:DURATION=1,URI="part12.1.ts"
#EXT-X-DATERANGE:ID="ad-2",START-DATE="2020-01-01T00:00:08.000Z"
#EXT-X-PART:DURATION=1,URI="part12.2.ts"
#EXTINF:4,
segment12.ts
#EXT-X-PART:DURATION=1,URI="part13.0.ts",INDEPENDENT=YES
#EXT-X-PART:DURATION=1,URI="part13.1.ts"
#EXTINF:4,
segment13.ts
#EXT-X-PART:DURATION=1,URI="part14.0.ts",INDEPENDENT=YES
#EXT-X-PART:DURATION=1,URI="part14.1.ts"
#EXTINF:4,
segment14.ts
"""


def _merged():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    first_delta = m3u8.M3U8(FIRST_DELTA)
    first_merged = m3u8.merge(previous, first_delta)
    second_delta = m3u8.M3U8(SECOND_DELTA)
    second_merged = m3u8.merge(first_merged, second_delta)
    return previous, first_delta, first_merged, second_delta, second_merged


def test_first_merge_restores_skipped_and_appends_new_segments():
    _, _, merged, _, _ = _merged()

    assert merged.media_sequence == 10
    assert merged.skip == {}
    assert [segment.media_sequence for segment in merged.segments] == [10, 11, 12, 13]
    assert merged.segments.uri == [
        "segment10.ts",
        "segment11.ts",
        "segment12.ts",
        "segment13.ts",
    ]


def test_second_merge_keeps_the_complete_window():
    *_, merged = _merged()

    assert merged.media_sequence == 10
    assert [segment.media_sequence for segment in merged.segments] == [
        10,
        11,
        12,
        13,
        14,
    ]
    assert merged.segments.uri == [
        "segment10.ts",
        "segment11.ts",
        "segment12.ts",
        "segment13.ts",
        "segment14.ts",
    ]
    # The rebuilt playlist is a complete playlist, the EXT-X-SKIP tag is gone.
    assert "EXT-X-SKIP" not in merged.dumps()


def test_key_map_daterange_and_part_associations_survive_both_merges():
    *_, merged = _merged()

    by_msn = {segment.media_sequence: segment for segment in merged.segments}

    # KEY and MAP are kept on every segment, including restored ones.
    for segment in merged.segments:
        assert segment.key is not None
        assert segment.key.uri == "https://priv.example.com/key.bin"
        assert segment.key.iv == "0X1"
        assert segment.init_section is not None
        assert segment.init_section.uri == "init.mp4"

    # Dateranges stay attached to the right segments.
    assert [daterange.id for daterange in by_msn[10].dateranges] == ["ad-1"]
    # ad-2 is attached to a partial segment of segment 12.
    assert [daterange.id for daterange in by_msn[12].dateranges] == []
    assert [daterange.id for daterange in by_msn[12].parts[2].dateranges] == ["ad-2"]

    # Partial segments survive, including those of the restored segment 12.
    assert [part.uri for part in by_msn[12].parts] == [
        "part12.0.ts",
        "part12.1.ts",
        "part12.2.ts",
    ]
    assert [part.uri for part in by_msn[14].parts] == [
        "part14.0.ts",
        "part14.1.ts",
    ]
    # A daterange attached to a partial segment stays attached to it.
    assert [daterange.id for daterange in by_msn[12].parts[2].dateranges] == ["ad-2"]
    assert by_msn[12].parts[0].independent == "YES"

    # Program date time of the restored first segment is preserved.
    assert by_msn[10].program_date_time is not None


def test_preload_hint_and_singleton_tags_come_from_the_delta():
    _, _, first_merged, _, _ = _merged()

    assert first_merged.preload_hint is not None
    assert first_merged.preload_hint.uri == "part14.0.ts"
    assert first_merged.part_inf.part_target == 1
    assert first_merged.server_control.can_skip_until == 24.0
    assert first_merged.discontinuity_sequence == 1


def test_merge_does_not_mutate_inputs():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    delta = m3u8.M3U8(FIRST_DELTA)
    previous_snapshot = copy.deepcopy(previous.data)
    delta_snapshot = copy.deepcopy(delta.data)

    merged = m3u8.merge(previous, delta)

    assert previous.data == previous_snapshot
    assert delta.data == delta_snapshot

    # Mutating the result must not affect the inputs, including nested
    # objects such as segments, parts and dateranges.
    merged.segments[0].uri = "changed.ts"
    merged.data["segments"][0]["uri"] = "changed-too.ts"
    assert previous.segments[0].uri == "segment10.ts"
    assert previous.data["segments"][0]["uri"] == "segment10.ts"

    restored_daterange = merged.data["segments"][0]["dateranges"][0]
    restored_daterange["duration"] = 999
    assert previous.data["segments"][0]["dateranges"][0]["duration"] == 4

    restored_part = merged.data["segments"][2]["parts"][0]
    restored_part["uri"] = "changed-part.ts"
    assert delta.data["segments"][1]["parts"][0]["uri"] == "part12.0.ts"


def test_merge_without_skip_tag_returns_independent_copy():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    full = m3u8.M3U8(
        FULL_PLAYLIST.replace("#EXT-X-MEDIA-SEQUENCE:10", "#EXT-X-MEDIA-SEQUENCE:11")
    )

    copied = m3u8.merge(previous, full)
    full.data["segments"][0]["uri"] = "mutated.ts"

    assert copied.segments[0].uri != "mutated.ts"
    assert copied.media_sequence == 11


def test_window_too_short_raises():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    delta = m3u8.M3U8(
        "#EXTM3U\n"
        "#EXT-X-MEDIA-SEQUENCE:20\n"
        "#EXT-X-SKIP:SKIPPED-SEGMENTS=5\n"
        "#EXTINF:4,\nsegment25.ts\n"
    )

    with pytest.raises(MergeError, match="window is too short"):
        m3u8.merge(previous, delta)


def test_sequence_regression_raises():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    delta = m3u8.M3U8(
        "#EXTM3U\n"
        "#EXT-X-MEDIA-SEQUENCE:5\n"
        "#EXT-X-SKIP:SKIPPED-SEGMENTS=1\n"
        "#EXTINF:4,\nsegment6.ts\n"
    )

    with pytest.raises(MergeError, match="regression"):
        m3u8.merge(previous, delta)


def test_duplicate_segment_raises():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    delta = m3u8.M3U8(FIRST_DELTA)
    delta.data["segments"].append(copy.deepcopy(delta.data["segments"][-1]))

    with pytest.raises(MergeError, match="duplicate Media Segment"):
        m3u8.merge(previous, delta)


def test_key_conflict_on_overlap_raises():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    conflicting = m3u8.M3U8(
        FIRST_DELTA.replace(
            'URI="https://priv.example.com/key.bin",IV=0X1',
            'URI="https://priv.example.com/other-key.bin",IV=0X1',
        )
    )

    with pytest.raises(MergeConflictError, match="EXT-X-KEY conflict"):
        m3u8.merge(previous, conflicting)


def test_map_conflict_on_overlap_raises():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    conflicting = m3u8.M3U8(
        FIRST_DELTA.replace('URI="init.mp4"', 'URI="other-init.mp4"')
    )

    with pytest.raises(MergeConflictError, match="EXT-X-MAP conflict"):
        m3u8.merge(previous, conflicting)


def test_discontinuity_sequence_conflict_raises():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    conflicting = m3u8.M3U8(
        FIRST_DELTA.replace(
            "#EXT-X-DISCONTINUITY-SEQUENCE:1",
            "#EXT-X-DISCONTINUITY-SEQUENCE:2",
        )
    )

    with pytest.raises(MergeConflictError, match="DISCONTINUITY-SEQUENCE mismatch"):
        m3u8.merge(previous, conflicting)


def test_invalid_skipped_segments_raises():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    delta = m3u8.M3U8(
        "#EXTM3U\n"
        "#EXT-X-MEDIA-SEQUENCE:10\n"
        "#EXT-X-SKIP:SKIPPED-SEGMENTS=0\n"
        "#EXTINF:4,\nsegment10.ts\n"
    )

    with pytest.raises(MergeError, match="SKIPPED-SEGMENTS must be a positive integer"):
        m3u8.merge(previous, delta)


def test_variant_playlist_cannot_be_merged():
    previous = m3u8.M3U8(FULL_PLAYLIST)
    variant = m3u8.M3U8("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nvariant.m3u8\n")

    with pytest.raises(MergeError, match="Media Playlists"):
        m3u8.merge(previous, variant)


def test_recently_removed_dateranges_are_dropped_and_others_consolidated():
    previous = m3u8.M3U8(
        "#EXTM3U\n"
        "#EXT-X-MEDIA-SEQUENCE:10\n"
        '#EXT-X-DATERANGE:ID="keep",START-DATE="2020-01-01T00:00:00Z"\n'
        '#EXT-X-DATERANGE:ID="drop",START-DATE="2020-01-01T00:00:01Z"\n'
        "#EXTINF:4,\nsegment10.ts\n"
        "#EXTINF:4,\nsegment11.ts\n"
    )
    delta = m3u8.M3U8(
        "#EXTM3U\n"
        "#EXT-X-MEDIA-SEQUENCE:10\n"
        '#EXT-X-SKIP:SKIPPED-SEGMENTS=1,RECENTLY-REMOVED-DATERANGES="drop"\n'
        '#EXT-X-DATERANGE:ID="keep",START-DATE="2020-01-01T00:00:00Z"\n'
        "#EXTINF:4,\nsegment11.ts\n"
        "#EXTINF:4,\nsegment12.ts\n"
    )

    merged = m3u8.merge(previous, delta)
    ids = [
        daterange.id for segment in merged.segments for daterange in segment.dateranges
    ]

    assert ids == ["keep"]
    assert 'ID="drop"' not in merged.dumps()


def test_conflicting_daterange_same_id_raises():
    previous = m3u8.M3U8(
        "#EXTM3U\n"
        "#EXT-X-MEDIA-SEQUENCE:10\n"
        '#EXT-X-DATERANGE:ID="r",START-DATE="2020-01-01T00:00:00Z",DURATION=10\n'
        "#EXTINF:4,\nsegment10.ts\n"
        "#EXTINF:4,\nsegment11.ts\n"
    )
    delta = m3u8.M3U8(
        "#EXTM3U\n"
        "#EXT-X-MEDIA-SEQUENCE:10\n"
        "#EXT-X-SKIP:SKIPPED-SEGMENTS=1\n"
        '#EXT-X-DATERANGE:ID="r",START-DATE="2020-01-01T00:00:00Z",DURATION=20\n'
        "#EXTINF:4,\nsegment11.ts\n"
        "#EXTINF:4,\nsegment12.ts\n"
    )

    with pytest.raises(MergeConflictError, match="conflicting EXT-X-DATERANGE"):
        m3u8.merge(previous, delta)


def test_daterange_augmentation_is_merged():
    # A later version of the same daterange may add attributes.
    previous = m3u8.M3U8(
        "#EXTM3U\n"
        "#EXT-X-MEDIA-SEQUENCE:10\n"
        '#EXT-X-DATERANGE:ID="r",START-DATE="2020-01-01T00:00:00Z"\n'
        "#EXTINF:4,\nsegment10.ts\n"
        "#EXTINF:4,\nsegment11.ts\n"
    )
    delta = m3u8.M3U8(
        "#EXTM3U\n"
        "#EXT-X-MEDIA-SEQUENCE:10\n"
        "#EXT-X-SKIP:SKIPPED-SEGMENTS=1\n"
        '#EXT-X-DATERANGE:ID="r",START-DATE="2020-01-01T00:00:00Z",DURATION=5\n'
        "#EXTINF:4,\nsegment11.ts\n"
        "#EXTINF:4,\nsegment12.ts\n"
    )

    merged = m3u8.merge(previous, delta)
    daterange = merged.segments[0].dateranges[0]

    assert daterange.id == "r"
    assert daterange.duration == 5


def test_merge_twice_yields_the_full_window_without_duplicates():
    *_, second_merged = _merged()

    assert [s.uri for s in second_merged.segments if s.uri] == [
        "segment10.ts",
        "segment11.ts",
        "segment12.ts",
        "segment13.ts",
        "segment14.ts",
    ]
    # Every restored segment keeps exactly one daterange attachment,
    # even though the same IDs appear in both input playlists.
    daterange_ids = [
        daterange.id
        for segment in second_merged.segments
        for daterange in segment.dateranges
    ] + [
        daterange.id
        for segment in second_merged.segments
        for part in segment.parts
        for daterange in part.dateranges
    ]
    assert sorted(daterange_ids) == ["ad-1", "ad-2"]
    assert "EXT-X-SKIP" not in second_merged.dumps()


def test_merged_playlist_round_trips_through_the_offline_parser():
    # The rebuilt complete playlist must be a valid, self-contained
    # playlist: dumping and re-parsing it (the regular offline parsing
    # path) yields the same window.
    *_, merged = _merged()

    reparsed = m3u8.M3U8(merged.dumps())

    assert reparsed.skip == {}
    assert reparsed.media_sequence == 10
    assert reparsed.segments.uri == [
        "segment10.ts",
        "segment11.ts",
        "segment12.ts",
        "segment13.ts",
        "segment14.ts",
    ]
    for segment in reparsed.segments:
        assert segment.key.uri == "https://priv.example.com/key.bin"
        assert segment.init_section.uri == "init.mp4"
    assert [part.uri for part in reparsed.segments[2].parts] == [
        "part12.0.ts",
        "part12.1.ts",
        "part12.2.ts",
    ]
    # Re-dumping the re-parsed playlist is stable.
    assert reparsed.dumps() == merged.dumps()


def test_append_only_delta_without_overlap():
    # The whole previous window is skipped and the delta only carries
    # brand new segments: restore everything, then append.
    previous = m3u8.M3U8(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:10\n"
        "#EXTINF:4,\nsegment10.ts\n"
        "#EXTINF:4,\nsegment11.ts\n"
    )
    delta = m3u8.M3U8(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:10\n"
        "#EXT-X-SKIP:SKIPPED-SEGMENTS=2\n"
        "#EXTINF:4,\nsegment12.ts\n"
        "#EXTINF:4,\nsegment13.ts\n"
    )

    merged = m3u8.merge(previous, delta)

    assert merged.segments.uri == [
        "segment10.ts",
        "segment11.ts",
        "segment12.ts",
        "segment13.ts",
    ]
    assert merged.media_sequence == 10


def test_explicit_method_none_key_is_preserved():
    previous = m3u8.M3U8(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:10\n"
        "#EXT-X-KEY:METHOD=NONE\n"
        "#EXTINF:4,\nsegment10.ts\n"
        "#EXTINF:4,\nsegment11.ts\n"
    )
    delta = m3u8.M3U8(
        "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:10\n"
        "#EXT-X-SKIP:SKIPPED-SEGMENTS=1\n"
        "#EXTINF:4,\nsegment11.ts\n"
        "#EXTINF:4,\nsegment12.ts\n"
    )

    merged = m3u8.merge(previous, delta)

    # The parser represents METHOD=NONE as a Key object whose method is
    # "NONE"; inheritance must keep it instead of dropping the tag.
    assert all(segment.key is not None for segment in merged.segments)
    assert all(segment.key.method == "NONE" for segment in merged.segments)


def test_base_uri_and_base_path_are_propagated_to_the_merged_playlist():
    previous = m3u8.M3U8(FULL_PLAYLIST, base_uri="https://cdn.example.com/path/")
    delta = m3u8.M3U8(FIRST_DELTA, base_uri="https://cdn.example.com/path/")

    merged = m3u8.merge(previous, delta)
    merged.base_path = "https://cdn.example.com/other"

    assert merged.base_uri == "https://cdn.example.com/path/"
    assert merged.segments[0].base_uri == "https://cdn.example.com/path/"
    assert merged.segments[0].uri == "https://cdn.example.com/other/segment10.ts"
    assert merged.preload_hint.uri == "https://cdn.example.com/other/part14.0.ts"
