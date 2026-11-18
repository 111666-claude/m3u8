# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

# Tests for m3u8.byterange: resolution of EXT-X-BYTERANGE values into
# closed-interval HTTP ranges, following the playlist order.

import playlists
import pytest

import m3u8
from m3u8.byterange import (
    MAX_INT64,
    ByteRangeError,
    parse_byterange,
    resolve_byteranges,
)


def _playlist(body, sequence=None):
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        "#EXT-X-TARGETDURATION:10",
    ]
    if sequence is not None:
        lines.append("#EXT-X-MEDIA-SEQUENCE:%d" % sequence)
    lines.append(body)
    lines.append("#EXT-X-ENDLIST")
    return m3u8.M3U8("\n".join(lines))


def _segments(*byteranges, uri="chunk.ts"):
    """Builds EXTINF/BYTERANGE/URI triples sharing one resource URI."""
    out = []
    for byterange in byteranges:
        out.append("#EXTINF:10,")
        if byterange is not None:
            out.append("#EXT-X-BYTERANGE:%s" % byterange)
        out.append(uri)
    return "\n".join(out)


def test_same_file_three_segments_inherit_offsets():
    obj = m3u8.M3U8(playlists.SAME_FILE_THREE_SEGMENTS_PLAYLIST)
    refs = obj.byteranges()

    assert [(r.uri, r.start, r.end, r.length) for r in refs] == [
        ("chunk.ts", 0, 99, 100),
        ("chunk.ts", 100, 299, 200),
        ("chunk.ts", 300, 599, 300),
    ]
    assert [r.http_range for r in refs] == [
        "bytes=0-99",
        "bytes=100-299",
        "bytes=300-599",
    ]
    # media sequence numbers follow EXT-X-MEDIA-SEQUENCE
    assert [r.sequence for r in refs] == [40, 41, 42]
    assert [r.kind for r in refs] == ["segment"] * 3


def test_explicit_offset_after_inherited_one():
    obj = _playlist(_segments("100@0", "200", "300@300"))
    refs = obj.byteranges()
    assert [(r.start, r.end) for r in refs] == [(0, 99), (100, 299), (300, 599)]


def test_uri_switch_does_not_inherit():
    obj = _playlist(
        "#EXTINF:10,\n#EXT-X-BYTERANGE:100@0\na.ts\n"
        "#EXTINF:10,\n#EXT-X-BYTERANGE:50@0\nb.ts\n"
        "#EXTINF:10,\n#EXT-X-BYTERANGE:60\nb.ts"
    )
    refs = obj.byteranges()
    assert [(r.uri, r.start, r.end) for r in refs] == [
        ("a.ts", 0, 99),
        ("b.ts", 0, 49),
        ("b.ts", 50, 109),
    ]


def test_uri_switch_with_omitted_offset_fails():
    obj = _playlist(
        "#EXTINF:10,\n#EXT-X-BYTERANGE:100@0\na.ts\n"
        "#EXTINF:10,\n#EXT-X-BYTERANGE:50\nb.ts",
        sequence=82400,
    )
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 82401
    assert "82401" in str(excinfo.value)


def test_map_is_an_independent_resource():
    obj = m3u8.M3U8(playlists.MAP_WITH_BYTERANGE_SEGMENTS_PLAYLIST)
    refs = obj.byteranges()

    assert [(r.kind, r.uri, r.start, r.end, r.length) for r in refs] == [
        ("map", "init.mp4", 0, 811, 812),
        ("segment", "main.mp4", 0, 999, 1000),
        ("segment", "main.mp4", 1000, 2999, 2000),
    ]


def test_segment_does_not_inherit_from_map():
    obj = _playlist(
        '#EXT-X-MAP:URI="init.mp4",BYTERANGE="812@0"\n'
        "#EXTINF:10,\n#EXT-X-BYTERANGE:100\nmain.mp4",
        sequence=7,
    )
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 7
    assert excinfo.value.kind == "segment"


def test_map_does_not_inherit_from_segment():
    obj = _playlist(
        "#EXTINF:10,\n#EXT-X-BYTERANGE:100@0\nmain.mp4\n"
        '#EXT-X-MAP:URI="init.mp4",BYTERANGE="812"\n'
        "#EXTINF:10,\n#EXT-X-BYTERANGE:100@100\nmain.mp4"
    )
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.kind == "map"


def test_map_inherits_from_previous_map_of_same_uri():
    obj = _playlist(
        '#EXT-X-MAP:URI="init.mp4",BYTERANGE="812@0"\n'
        "#EXTINF:10,\nfirst.mp4\n"
        '#EXT-X-MAP:URI="init.mp4",BYTERANGE="900"\n'
        "#EXTINF:10,\nsecond.mp4"
    )
    refs = obj.byteranges()
    assert [(r.kind, r.start, r.end) for r in refs] == [
        ("map", 0, 811),
        ("map", 812, 1711),
    ]


def test_parts_share_the_media_resource_chain():
    obj = m3u8.M3U8(playlists.PARTS_WITH_BYTERANGES_PLAYLIST)
    refs = obj.byteranges()

    # fs270.mp4 has no BYTERANGE: whole file, no ref, and it must not
    # become an inheritance predecessor.
    assert [(r.kind, r.uri, r.start, r.end, r.length) for r in refs] == [
        ("part", "fs271.mp4", 0, 19999, 20000),
        ("part", "fs271.mp4", 20000, 42999, 23000),
        ("part", "fs271.mp4", 43000, 60999, 18000),
        ("segment", "fs271.mp4", 61000, 121999, 61000),
    ]
    assert [r.sequence for r in refs] == [271, 271, 271, 271]
    assert refs[0].http_range == "bytes=0-19999"


def test_whole_file_entry_resets_inheritance():
    obj = _playlist(_segments("100@0", None, "50"), sequence=3)
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    # the offset-less third segment lost its predecessor to the
    # whole-file second segment
    assert excinfo.value.sequence == 5


def test_whole_file_entry_emits_no_ref():
    obj = _playlist(_segments("100@0", None, "50@100"))
    refs = obj.byteranges()
    assert [(r.start, r.end) for r in refs] == [(0, 99), (100, 149)]


def test_zero_length_reports_segment_sequence():
    obj = _playlist(_segments("0@100"), sequence=82400)
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 82400
    assert "82400" in str(excinfo.value)


def test_negative_length_reports_segment_sequence():
    obj = _playlist(_segments("-5@10"), sequence=9)
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 9


@pytest.mark.parametrize(
    "value",
    ["abc", "100@x", "@100", "100@", "10 0@5", "1.5@0", "", "100@1@2"],
)
def test_malformed_values_report_segment_sequence(value):
    obj = _playlist(_segments("100@0", value), sequence=11)
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 12


def test_negative_offset_is_rejected():
    obj = _playlist(_segments("100@-5"), sequence=4)
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 4


def test_overlap_conflict_reports_segment_sequence():
    obj = _playlist(_segments("100@100", "50@150"), sequence=20)
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 21
    assert "overlap" in str(excinfo.value)


def test_adjacent_and_gapped_ranges_are_not_overlaps():
    obj = _playlist(_segments("100@0", "100@100", "100@500"))
    refs = obj.byteranges()
    assert [(r.start, r.end) for r in refs] == [(0, 99), (100, 199), (500, 599)]


def test_overflow_reports_segment_sequence():
    huge = "10@%d" % MAX_INT64
    obj = _playlist(_segments(huge), sequence=30)
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 30
    assert "overflow" in str(excinfo.value)


def test_length_overflow_is_rejected():
    obj = _playlist(_segments("%d@0" % (MAX_INT64 + 1)), sequence=31)
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 31


def test_inherited_offset_overflow_is_rejected():
    obj = _playlist(_segments("%d@0" % MAX_INT64, "10"), sequence=32)
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 33


def test_max_int64_boundary_is_accepted():
    obj = _playlist(_segments("%d@0" % MAX_INT64, "1"))
    refs = obj.byteranges()
    assert refs[-1].end == MAX_INT64


def test_parse_byterange_returns_offset_and_length():
    assert parse_byterange("100@5") == (5, 100)
    assert parse_byterange("100") == (None, 100)


def test_resolve_byteranges_accepts_segment_iterable():
    obj = m3u8.M3U8(playlists.SAME_FILE_THREE_SEGMENTS_PLAYLIST)
    refs = resolve_byteranges(obj.segments)
    assert [r.http_range for r in refs] == [
        "bytes=0-99",
        "bytes=100-299",
        "bytes=300-599",
    ]


def test_byterange_error_exposes_sequence_and_kind():
    err = ByteRangeError("boom", sequence=7, kind="part")
    assert err.sequence == 7
    assert err.kind == "part"
    assert "segment 7" in str(err)


def test_playlists_without_byteranges_resolve_to_nothing():
    obj = m3u8.M3U8(playlists.SIMPLE_PLAYLIST)
    assert obj.byteranges() == []


def test_existing_byterange_attributes_are_untouched():
    obj = m3u8.M3U8(playlists.MAP_URI_PLAYLIST_WITH_BYTERANGE)
    assert obj.segments[0].init_section.byterange == "812@0"
    refs = obj.byteranges()
    assert [(r.kind, r.uri, r.start, r.end, r.length) for r in refs] == [
        ("map", "main.mp4", 0, 811, 812),
        ("map", "main2.mp4", 0, 911, 912),
    ]


def test_legacy_fixture_with_overlapping_ranges_is_reported():
    # PLAYLIST_USING_BYTERANGES carries explicit offsets whose third
    # range overlaps the second one; resolution must flag it.
    obj = m3u8.M3U8(playlists.PLAYLIST_USING_BYTERANGES)
    assert obj.segments[0].byterange == "76242@0"
    with pytest.raises(ByteRangeError) as excinfo:
        obj.byteranges()
    assert excinfo.value.sequence == 2
