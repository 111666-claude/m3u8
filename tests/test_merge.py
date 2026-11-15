# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

# Tests for merging a Playlist Delta Update (EXT-X-SKIP) with the
# previously known version of a Media Playlist.

import pytest

import m3u8
from m3u8 import PlaylistMergeError
from m3u8.merge import merge

HEADER = """#EXTM3U
#EXT-X-VERSION:9
#EXT-X-TARGETDURATION:4
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,CAN-SKIP-UNTIL=24.0
"""


def simple_playlist(first_msn, uris, skip=None, extra_header=""):
    lines = [HEADER, extra_header, "#EXT-X-MEDIA-SEQUENCE:%d" % first_msn]
    if skip is not None:
        skip_line = "#EXT-X-SKIP:SKIPPED-SEGMENTS=%d" % skip
        lines.append(skip_line)
    for uri in uris:
        lines.append("#EXTINF:4.0,")
        lines.append(uri)
    return m3u8.loads("\n".join(lines))


def test_merge_restores_skipped_segments_in_order():
    previous = simple_playlist(0, ["seg%d.ts" % i for i in range(6)])
    # MEDIA-SEQUENCE points at the first segment replaced by EXT-X-SKIP;
    # the first segment listed by the delta starts SKIPPED-SEGMENTS later.
    current = simple_playlist(0, ["seg2.ts", "seg3.ts", "seg4.ts", "seg5.ts"], skip=2)

    merged = merge(previous, current)

    assert [segment.media_sequence for segment in merged.segments] == [
        0,
        1,
        2,
        3,
        4,
        5,
    ]
    assert [segment.uri for segment in merged.segments] == [
        "seg0.ts",
        "seg1.ts",
        "seg2.ts",
        "seg3.ts",
        "seg4.ts",
        "seg5.ts",
    ]
    assert merged.media_sequence == 0
    # restored (0, 1) come from the previous playlist, listed ones from current
    assert merged.segments[0].uri == previous.segments[0].uri
    assert merged.segments[2].uri == current.segments[0].uri


def test_merge_includes_new_segments_and_crops_the_old_window():
    previous = simple_playlist(0, ["seg%d.ts" % i for i in range(6)])
    current = simple_playlist(0, ["seg3.ts", "seg4.ts", "seg5.ts", "seg6.ts"], skip=3)

    merged = merge(previous, current)

    assert [segment.media_sequence for segment in merged.segments] == [
        0,
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    assert [segment.uri for segment in merged.segments] == [
        "seg0.ts",
        "seg1.ts",
        "seg2.ts",
        "seg3.ts",
        "seg4.ts",
        "seg5.ts",
        "seg6.ts",
    ]


def test_merge_crops_segments_older_than_the_delta_window():
    previous = simple_playlist(0, ["seg%d.ts" % i for i in range(8)])
    # the server already cropped MSNs 0..3 from the window; the delta
    # replaces MSNs 4 and 5 with EXT-X-SKIP and lists 6, 7 and 8
    current = simple_playlist(4, ["seg6.ts", "seg7.ts", "seg8.ts"], skip=2)

    merged = merge(previous, current)

    assert [segment.media_sequence for segment in merged.segments] == [
        4,
        5,
        6,
        7,
        8,
    ]
    assert merged.media_sequence == 4
    assert [segment.uri for segment in merged.segments] == [
        "seg4.ts",
        "seg5.ts",
        "seg6.ts",
        "seg7.ts",
        "seg8.ts",
    ]


def test_merge_without_skip_behaves_like_a_reload():
    previous = simple_playlist(0, ["seg%d.ts" % i for i in range(4)])
    current = simple_playlist(2, ["seg2.ts", "seg3.ts", "seg4.ts"])

    merged = merge(previous, current)

    assert [segment.media_sequence for segment in merged.segments] == [
        2,
        3,
        4,
    ]
    assert merged.skip is None


def test_merge_skips_nothing_when_skipped_segments_is_zero():
    previous = simple_playlist(0, ["seg0.ts", "seg1.ts", "seg2.ts"])
    current = simple_playlist(0, ["seg0.ts", "seg1.ts", "seg2.ts"], skip=0)

    merged = merge(previous, current)

    assert [segment.media_sequence for segment in merged.segments] == [0, 1, 2]
    assert merged.media_sequence == 0


def test_merge_does_not_duplicate_low_latency_parts():
    full = m3u8.M3U8(LOW_LATENCY_FULL)
    delta = m3u8.M3U8(LOW_LATENCY_DELTA)

    merged = merge(full, delta)

    segment_271 = next(
        segment for segment in merged.segments if segment.media_sequence == 271
    )
    # the delta's authoritative PART list replaces the previous one
    assert [part.uri for part in segment_271.parts] == [
        "filePart271.%d.ts" % i for i in range(4)
    ]
    segment_273 = merged.segments[-1]
    assert segment_273.uri is None
    assert [part.uri for part in segment_273.parts] == [
        "filePart273.%d.ts" % i for i in range(4)
    ]
    # no PART URI appears twice in the serialized window
    dump = merged.dumps()
    part_lines = [line for line in dump.splitlines() if line.startswith("#EXT-X-PART")]
    assert len(part_lines) == len(set(part_lines))


def test_merge_replaces_updated_parts_of_the_first_listed_segment():
    full = m3u8.M3U8(LOW_LATENCY_FULL)
    delta = m3u8.M3U8(LOW_LATENCY_DELTA)

    merged = merge(full, delta)
    segment_273 = merged.segments[-1]

    # previous playlist only knew parts 0..2 plus a preload hint for 3;
    # the delta brings parts 0..3 and a hint for 4
    assert len(segment_273.parts) == 4
    assert merged.preload_hint.uri == "filePart273.4.ts"


def test_merge_keeps_key_and_map_attached_to_restored_segments():
    previous = m3u8.loads(PLAYLIST_WITH_KEY_ROTATION_PREVIOUS)
    current = m3u8.loads(PLAYLIST_WITH_KEY_ROTATION_DELTA)

    merged = merge(previous, current)

    assert [key.uri if key else None for key in merged.keys] == [
        "key0.bin",
        "key1.bin",
    ]
    assert [init.uri for init in merged.segment_map] == [
        "init0.mp4",
        "init1.mp4",
    ]
    by_msn = {segment.media_sequence: segment for segment in merged.segments}
    for msn in (0, 1, 2, 3):
        assert by_msn[msn].key.uri == "key0.bin"
        assert by_msn[msn].init_section.uri == "init0.mp4"
    for msn in (4, 5, 6, 7, 8):
        assert by_msn[msn].key.uri == "key1.bin"
        assert by_msn[msn].init_section.uri == "init1.mp4"

    dump = merged.dumps()
    assert dump.count("#EXT-X-KEY") == 2
    assert dump.count("#EXT-X-MAP") == 2
    assert "#EXT-X-SKIP" not in dump


def test_merge_keeps_discontinuity_flag_and_sequence():
    previous = m3u8.loads(PLAYLIST_WITH_KEY_ROTATION_PREVIOUS)
    current = m3u8.loads(
        PLAYLIST_WITH_KEY_ROTATION_DELTA.replace(
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-DISCONTINUITY-SEQUENCE:9",
        )
    )
    previous.discontinuity_sequence = 9

    merged = merge(previous, current)

    assert merged.discontinuity_sequence == 9
    flags = [segment.discontinuity for segment in merged.segments]
    assert flags == [False, False, False, False, True, False, False, False, False]
    assert merged.dumps().count("#EXT-X-DISCONTINUITY\n") == 1


def test_merge_rejects_incompatible_discontinuity_sequence():
    previous = simple_playlist(0, ["seg0.ts"])
    previous.discontinuity_sequence = 5
    current = simple_playlist(0, ["seg0.ts", "seg1.ts"])
    current.discontinuity_sequence = 6

    with pytest.raises(PlaylistMergeError, match="DISCONTINUITY-SEQUENCE"):
        merge(previous, current)


def test_merge_preserves_program_date_time_range():
    previous = m3u8.loads(PLAYLIST_WITH_PDT_PREVIOUS)
    current = m3u8.loads(PLAYLIST_WITH_PDT_DELTA)

    merged = merge(previous, current)

    assert str(merged.program_date_time) == "2021-05-01 10:00:00+00:00"
    first = merged.segments[0]
    assert first.media_sequence == 10
    assert str(first.program_date_time) == "2021-05-01 10:00:00+00:00"
    assert merged.dumps().count("#EXT-X-PROGRAM-DATE-TIME") == 1


def test_merge_propagates_program_date_time_after_crop_and_roundtrip():
    # The segment carrying the explicit EXT-X-PROGRAM-DATE-TIME is cropped
    # out of the window; its calculated time must still be recoverable.
    previous = m3u8.loads(
        """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PROGRAM-DATE-TIME:2021-01-01T00:00:00.000Z
#EXTINF:4.0,
s0.ts
#EXTINF:4.0,
s1.ts
#EXTINF:4.0,
s2.ts
"""
    )
    current = m3u8.loads(
        """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:1
#EXT-X-SKIP:SKIPPED-SEGMENTS=1
#EXTINF:4.0,
s1.ts
#EXTINF:4.0,
s2.ts
#EXTINF:4.0,
s3.ts
"""
    )

    merged = merge(previous, current)
    reloaded = m3u8.loads(merged.dumps())

    assert str(merged.program_date_time) == "2021-01-01 00:00:04+00:00"
    assert merged.dumps().count("#EXT-X-PROGRAM-DATE-TIME") == 1
    assert str(reloaded.segments[0].program_date_time) == ("2021-01-01 00:00:04+00:00")
    assert reloaded.dumps().count("#EXT-X-PROGRAM-DATE-TIME") == 1


def test_merge_removes_recently_removed_dateranges_but_keeps_others():
    previous = m3u8.loads(PLAYLIST_WITH_DATERANGES_PREVIOUS)
    current = m3u8.loads(PLAYLIST_WITH_DATERANGES_DELTA)

    merged = merge(previous, current)
    dump = merged.dumps()

    assert 'ID="keep"' in dump
    assert 'ID="gone"' not in dump
    # the input playlist must not be modified
    assert 'ID="gone"' in previous.dumps()


def test_merge_error_when_history_does_not_cover_skipped_segments():
    previous = simple_playlist(2, ["seg2.ts", "seg3.ts"])
    current = simple_playlist(0, ["seg4.ts"], skip=4)

    with pytest.raises(PlaylistMergeError, match="skipped Media Segment"):
        merge(previous, current)


def test_merge_error_when_previous_playlist_is_empty_with_skip():
    current = simple_playlist(0, ["seg4.ts"], skip=4)

    with pytest.raises(PlaylistMergeError, match="no segment"):
        merge(m3u8.M3U8(), current)


def test_merge_error_on_media_sequence_regression():
    previous = simple_playlist(0, ["seg%d.ts" % i for i in range(6)])
    # the new window ends before the previous complete window (MSN 2 < 5)
    current = simple_playlist(0, ["seg%d.ts" % i for i in range(3)], skip=1)

    with pytest.raises(PlaylistMergeError, match="moved backwards"):
        merge(previous, current)


def test_merge_error_on_gap_between_two_full_reloads():
    previous = simple_playlist(0, ["seg0.ts", "seg1.ts"])
    current = simple_playlist(5, ["seg5.ts"])

    with pytest.raises(PlaylistMergeError, match="Media Sequence Number"):
        merge(previous, current)


def test_merge_rejects_variant_playlists():
    media = simple_playlist(0, ["seg0.ts"])
    variant = m3u8.loads(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1280000\nlow/video.m3u8\n"
    )

    with pytest.raises(PlaylistMergeError, match="Master"):
        merge(variant, media)
    with pytest.raises(PlaylistMergeError, match="Master"):
        merge(media, variant)


def test_merge_rejects_non_m3u8_objects():
    with pytest.raises(PlaylistMergeError, match="M3U8 object"):
        merge("not a playlist", simple_playlist(0, ["seg0.ts"]))


def test_merge_does_not_mutate_inputs():
    previous = simple_playlist(0, ["seg%d.ts" % i for i in range(6)])
    current = simple_playlist(0, ["seg3.ts", "seg4.ts", "seg5.ts"], skip=3)
    previous_snapshot = previous.dumps()
    current_snapshot = current.dumps()
    current_labels = [segment.media_sequence for segment in current.segments]

    merge(previous, current)

    assert previous.dumps() == previous_snapshot
    assert current.dumps() == current_snapshot
    # the parser's original (uncorrected) labels must remain untouched
    assert [segment.media_sequence for segment in current.segments] == current_labels


def test_merge_result_is_serializable_and_survives_dump_load_roundtrip():
    full = m3u8.M3U8(LOW_LATENCY_FULL)
    delta = m3u8.M3U8(LOW_LATENCY_DELTA)

    merged = merge(full, delta)
    reloaded = m3u8.loads(merged.dumps())

    assert [segment.media_sequence for segment in reloaded.segments] == [
        segment.media_sequence for segment in merged.segments
    ]
    assert [segment.uri for segment in reloaded.segments] == [
        segment.uri for segment in merged.segments
    ]
    assert reloaded.skip in (None, False) or not reloaded.skip
    assert "#EXT-X-SKIP" not in reloaded.dumps()
    assert reloaded.media_sequence == merged.media_sequence


def test_merge_can_be_chained_with_a_reloaded_playlist():
    full = m3u8.M3U8(LOW_LATENCY_FULL)
    delta = m3u8.M3U8(LOW_LATENCY_DELTA)

    first = merge(full, delta)
    second = m3u8.loads(first.dumps())
    next_update = m3u8.loads(LOW_LATENCY_NEXT_RELOAD)

    merged_again = merge(second, next_update)

    assert [segment.media_sequence for segment in merged_again.segments] == [
        268,
        269,
        270,
        271,
        272,
        273,
        274,
    ]
    # the previously parts-only segment 273 is now a complete segment
    by_msn = {segment.media_sequence: segment for segment in merged_again.segments}
    assert by_msn[273].uri == "fileSequence273.ts"
    assert [part.uri for part in by_msn[274].parts] == ["filePart274.0.ts"]
    assert merged_again.preload_hint.uri == "filePart274.1.ts"


def test_merge_keeps_relative_uris_resolvable_against_their_own_base_uri():
    full = m3u8.loads(LOW_LATENCY_FULL, uri="https://example.com/2m/waitForMSN.php")
    delta = m3u8.loads(LOW_LATENCY_DELTA, uri="https://example.com/2m/waitForMSN.php")

    merged = merge(full, delta)

    assert merged.segments[0].absolute_uri == (
        "https://example.com/2m/fileSequence266.mp4"
    )
    assert merged.segments[3].absolute_uri == (
        "https://example.com/2m/fileSequence269.ts"
    )
    assert merged.segment_map[0].absolute_uri == ("https://example.com/2m/init.mp4")
    # raw URIs stay relative in the serialized playlist
    assert merged.segments[0].uri == "fileSequence266.mp4"


def test_merge_method_on_m3u8_class_matches_module_function():
    full = m3u8.M3U8(LOW_LATENCY_FULL)
    delta = m3u8.M3U8(LOW_LATENCY_DELTA)

    via_method = full.merge(delta)
    via_function = merge(full, delta)

    assert via_method.dumps() == via_function.dumps()


def test_merge_carries_playlist_level_tags_from_the_delta():
    full = m3u8.M3U8(LOW_LATENCY_FULL)
    delta = m3u8.M3U8(LOW_LATENCY_DELTA)

    merged = merge(full, delta)

    assert merged.target_duration == delta.target_duration
    assert merged.version == delta.version
    assert merged.server_control.can_skip_until == 12.0
    assert merged.part_inf.part_target == delta.part_inf.part_target
    assert len(merged.rendition_reports) == 2
    assert merged.preload_hint.uri == delta.preload_hint.uri


def test_deep_copied_segments_are_independent_from_inputs():
    previous = simple_playlist(0, ["seg0.ts", "seg1.ts", "seg2.ts"])
    current = simple_playlist(0, ["seg2.ts", "seg3.ts"], skip=2)
    previous_parts = len(previous.segments[2].parts)

    merged = merge(previous, current)
    merged.segments[0].uri = "tampered.ts"

    assert previous.segments[2].uri == "seg2.ts"
    assert len(previous.segments[2].parts) == previous_parts
    assert current.segments[0].uri == "seg2.ts"


LOW_LATENCY_FULL = """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-VERSION:6
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0,CAN-SKIP-UNTIL=24.0
#EXT-X-PART-INF:PART-TARGET=0.33334
#EXT-X-MEDIA-SEQUENCE:264
#EXT-X-PROGRAM-DATE-TIME:2019-02-14T02:13:28.106Z
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4.00008,
fileSequence264.mp4
#EXTINF:4.00008,
fileSequence265.mp4
#EXTINF:4.00008,
fileSequence266.mp4
#EXTINF:4.00008,
fileSequence267.mp4
#EXTINF:4.00008,
fileSequence268.mp4
#EXTINF:4.00008,
fileSequence269.mp4
#EXTINF:4.00008,
fileSequence270.mp4
#EXT-X-PART:DURATION=0.33334,URI="filePart271.0.mp4"
#EXT-X-PART:DURATION=0.33334,URI="filePart271.1.mp4"
#EXT-X-PART:DURATION=0.33334,URI="filePart271.2.mp4"
#EXTINF:4.00008,
fileSequence271.mp4
#EXT-X-PROGRAM-DATE-TIME:2019-02-14T02:14:00.106Z
#EXT-X-PART:DURATION=0.33334,URI="filePart272.a.mp4"
#EXT-X-PART:DURATION=0.33334,URI="filePart272.b.mp4"
#EXTINF:4.00008,
fileSequence272.mp4
#EXT-X-PART:DURATION=0.33334,URI="filePart273.0.mp4",INDEPENDENT=YES
#EXT-X-PART:DURATION=0.33334,URI="filePart273.1.mp4"
#EXT-X-PART:DURATION=0.33334,URI="filePart273.2.mp4"
#EXT-X-PRELOAD-HINT:TYPE=PART,URI="filePart273.3.mp4"
#EXT-X-RENDITION-REPORT:URI="../1M/waitForMSN.php",LAST-MSN=273,LAST-PART=2
#EXT-X-RENDITION-REPORT:URI="../4M/waitForMSN.php",LAST-MSN=273,LAST-PART=1
"""

LOW_LATENCY_DELTA = """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-VERSION:9
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0,CAN-SKIP-UNTIL=12.0
#EXT-X-PART-INF:PART-TARGET=0.33334
#EXT-X-MEDIA-SEQUENCE:266
#EXT-X-SKIP:SKIPPED-SEGMENTS=3
#EXTINF:4.00008,
fileSequence269.ts
#EXTINF:4.00008,
fileSequence270.ts
#EXT-X-PART:DURATION=0.33334,URI="filePart271.0.ts"
#EXT-X-PART:DURATION=0.33334,URI="filePart271.1.ts"
#EXT-X-PART:DURATION=0.33334,URI="filePart271.2.ts"
#EXT-X-PART:DURATION=0.33334,URI="filePart271.3.ts"
#EXTINF:4.00008,
fileSequence271.ts
#EXT-X-PROGRAM-DATE-TIME:2019-02-14T02:14:00.106Z
#EXT-X-PART:DURATION=0.33334,URI="filePart272.a.ts"
#EXT-X-PART:DURATION=0.33334,URI="filePart272.b.ts"
#EXTINF:4.00008,
fileSequence272.ts
#EXT-X-PART:DURATION=0.33334,URI="filePart273.0.ts",INDEPENDENT=YES
#EXT-X-PART:DURATION=0.33334,URI="filePart273.1.ts"
#EXT-X-PART:DURATION=0.33334,URI="filePart273.2.ts"
#EXT-X-PART:DURATION=0.33334,URI="filePart273.3.ts"
#EXT-X-PRELOAD-HINT:TYPE=PART,URI="filePart273.4.ts"
#EXT-X-RENDITION-REPORT:URI="../1M/waitForMSN.php",LAST-MSN=273,LAST-PART=3
#EXT-X-RENDITION-REPORT:URI="../4M/waitForMSN.php",LAST-MSN=273,LAST-PART=3
"""

LOW_LATENCY_NEXT_RELOAD = """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-VERSION:9
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0,CAN-SKIP-UNTIL=12.0
#EXT-X-PART-INF:PART-TARGET=0.33334
#EXT-X-MEDIA-SEQUENCE:268
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4.00008,
fileSequence268.mp4
#EXTINF:4.00008,
fileSequence269.ts
#EXTINF:4.00008,
fileSequence270.ts
#EXTINF:4.00008,
fileSequence271.ts
#EXTINF:4.00008,
fileSequence272.ts
#EXTINF:4.00008,
fileSequence273.ts
#EXT-X-PART:DURATION=0.33334,URI="filePart274.0.ts",INDEPENDENT=YES
#EXT-X-PRELOAD-HINT:TYPE=PART,URI="filePart274.1.ts"
"""

PLAYLIST_WITH_KEY_ROTATION_PREVIOUS = """#EXTM3U
#EXT-X-VERSION:10
#EXT-X-TARGETDURATION:6
#EXT-X-SERVER-CONTROL:CAN-SKIP-UNTIL=36
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-MAP:URI="init0.mp4"
#EXT-X-KEY:METHOD=AES-128,URI="key0.bin",IV=0X0
#EXTINF:4.0,
seg0.ts
#EXTINF:4.0,
seg1.ts
#EXTINF:4.0,
seg2.ts
#EXTINF:4.0,
seg3.ts
#EXT-X-DISCONTINUITY
#EXT-X-MAP:URI="init1.mp4"
#EXT-X-KEY:METHOD=AES-128,URI="key1.bin",IV=0X1
#EXTINF:4.0,
seg4.ts
#EXTINF:4.0,
seg5.ts
#EXTINF:4.0,
seg6.ts
#EXTINF:4.0,
seg7.ts
"""

PLAYLIST_WITH_KEY_ROTATION_DELTA = """#EXTM3U
#EXT-X-VERSION:10
#EXT-X-TARGETDURATION:6
#EXT-X-SERVER-CONTROL:CAN-SKIP-UNTIL=36
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-SKIP:SKIPPED-SEGMENTS=4
#EXT-X-DISCONTINUITY
#EXT-X-MAP:URI="init1.mp4"
#EXT-X-KEY:METHOD=AES-128,URI="key1.bin",IV=0X1
#EXTINF:4.0,
seg4.ts
#EXTINF:4.0,
seg5.ts
#EXTINF:4.0,
seg6.ts
#EXTINF:4.0,
seg7.ts
#EXTINF:4.0,
seg8.ts
"""


PLAYLIST_WITH_PDT_PREVIOUS = """#EXTM3U
#EXT-X-VERSION:10
#EXT-X-TARGETDURATION:4
#EXT-X-SERVER-CONTROL:CAN-SKIP-UNTIL=24
#EXT-X-MEDIA-SEQUENCE:10
#EXT-X-PROGRAM-DATE-TIME:2021-05-01T10:00:00.000Z
#EXTINF:4.0,
a10.ts
#EXTINF:4.0,
a11.ts
#EXTINF:4.0,
a12.ts
"""

PLAYLIST_WITH_PDT_DELTA = """#EXTM3U
#EXT-X-VERSION:10
#EXT-X-TARGETDURATION:4
#EXT-X-SERVER-CONTROL:CAN-SKIP-UNTIL=24
#EXT-X-MEDIA-SEQUENCE:10
#EXT-X-SKIP:SKIPPED-SEGMENTS=1
#EXTINF:4.0,
a11.ts
#EXTINF:4.0,
a12.ts
#EXTINF:4.0,
a13.ts
"""

PLAYLIST_WITH_DATERANGES_PREVIOUS = """#EXTM3U
#EXT-X-VERSION:10
#EXT-X-TARGETDURATION:6
#EXT-X-SERVER-CONTROL:CAN-SKIP-UNTIL=36,CAN-SKIP-DATERANGES=YES
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-DATERANGE:ID="keep",START-DATE="2020-01-01T00:00:00.000Z"
#EXTINF:4.0,
seg0.ts
#EXT-X-DATERANGE:ID="gone",START-DATE="2020-01-01T00:00:04.000Z"
#EXTINF:4.0,
seg1.ts
#EXTINF:4.0,
seg2.ts
#EXTINF:4.0,
seg3.ts
"""

PLAYLIST_WITH_DATERANGES_DELTA = """#EXTM3U
#EXT-X-VERSION:10
#EXT-X-TARGETDURATION:6
#EXT-X-SERVER-CONTROL:CAN-SKIP-UNTIL=36,CAN-SKIP-DATERANGES=YES
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-SKIP:SKIPPED-SEGMENTS=2,RECENTLY-REMOVED-DATERANGES="gone"
#EXTINF:4.0,
seg2.ts
#EXTINF:4.0,
seg3.ts
#EXTINF:4.0,
seg4.ts
"""
