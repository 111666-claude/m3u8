# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import dataclasses
import textwrap

import playlists
import pytest

import m3u8
from m3u8 import Diagnostic, validate_playlist
from m3u8.validation import (
    RULE_BYTERANGE_INVALID,
    RULE_BYTERANGE_OFFSET_REQUIRED,
    RULE_EXTINF_EXCEEDS_TARGET_DURATION,
    RULE_FMP4_MAP_MISSING,
    RULE_MAP_BYTERANGE_INVALID,
    RULE_MAP_URI_MISSING,
    RULE_VOD_MISSING_ENDLIST,
    RULES,
)

MULTI_ERROR_PLAYLIST = textwrap.dedent(
    """\
    #EXTM3U
    #EXT-X-VERSION:7
    #EXT-X-TARGETDURATION:4
    #EXT-X-MEDIA-SEQUENCE:10
    #EXT-X-PLAYLIST-TYPE:VOD
    #EXT-X-MAP:URI="init.mp4"
    #EXTINF:5.0,
    seg1.m4s
    #EXT-X-BYTERANGE:900
    seg2.m4s
    #EXT-X-BYTERANGE:1000
    seg2.m4s
    #EXTINF:8,
    seg3.m4s
    """
)

VALID_VOD_PLAYLIST = textwrap.dedent(
    """\
    #EXTM3U
    #EXT-X-VERSION:7
    #EXT-X-TARGETDURATION:6
    #EXT-X-MEDIA-SEQUENCE:100
    #EXT-X-PLAYLIST-TYPE:VOD
    #EXT-X-MAP:URI="init.mp4"
    #EXTINF:6.0,
    seg100.m4s
    #EXT-X-BYTERANGE:1000@0
    seg100.m4s
    #EXTINF:5.5,
    seg101.m4s
    #EXT-X-ENDLIST
    """
)

VALID_LIVE_PLAYLIST = textwrap.dedent(
    """\
    #EXTM3U
    #EXT-X-VERSION:3
    #EXT-X-TARGETDURATION:8
    #EXT-X-MEDIA-SEQUENCE:2680
    #EXTINF:8,
    https://priv.example.com/fileSequence2680.ts
    #EXTINF:8,
    https://priv.example.com/fileSequence2681.ts
    #EXTINF:8,
    https://priv.example.com/fileSequence2682.ts
    """
)


def rules(diagnostics):
    return [diagnostic.rule for diagnostic in diagnostics]


def lines(diagnostics):
    return [diagnostic.line for diagnostic in diagnostics]


def test_valid_vod_playlist_has_no_diagnostics():
    assert validate_playlist(VALID_VOD_PLAYLIST) == []


def test_valid_live_playlist_has_no_diagnostics():
    assert validate_playlist(VALID_LIVE_PLAYLIST) == []


def test_multi_error_playlist_reports_all_issues_in_playlist_order():
    diagnostics = validate_playlist(MULTI_ERROR_PLAYLIST)

    assert rules(diagnostics) == [
        RULE_VOD_MISSING_ENDLIST,
        RULE_EXTINF_EXCEEDS_TARGET_DURATION,
        RULE_BYTERANGE_OFFSET_REQUIRED,
        RULE_EXTINF_EXCEEDS_TARGET_DURATION,
    ]
    assert lines(diagnostics) == [5, 7, 9, 13]


def test_diagnostics_carry_segment_position_sequence_and_uri():
    diagnostics = validate_playlist(MULTI_ERROR_PLAYLIST)
    target = next(
        d for d in diagnostics if d.rule == RULE_EXTINF_EXCEEDS_TARGET_DURATION
    )

    assert target.severity == "error"
    assert target.tag == "#EXTINF"
    assert target.segment_index == 0
    assert target.media_sequence == 10
    assert target.uri == "seg1.m4s"
    assert target.line == 7

    byterange = next(d for d in diagnostics if d.rule == RULE_BYTERANGE_OFFSET_REQUIRED)
    assert byterange.tag == "#EXT-X-BYTERANGE"
    assert byterange.segment_index == 1
    assert byterange.media_sequence == 11
    assert byterange.uri == "seg2.m4s"
    assert byterange.line == 9


def test_vod_missing_endlist_points_at_playlist_type_tag():
    diagnostics = validate_playlist(MULTI_ERROR_PLAYLIST)
    diagnostic = diagnostics[0]

    assert diagnostic.rule == RULE_VOD_MISSING_ENDLIST
    assert diagnostic.severity == "error"
    assert diagnostic.line == 5
    assert diagnostic.tag == "#EXT-X-PLAYLIST-TYPE"


def test_extinf_within_target_duration_is_accepted_after_rounding():
    playlist = _replace(VALID_VOD_PLAYLIST, "#EXTINF:5.5,", "#EXTINF:6.49,")

    assert validate_playlist(playlist) == []


def test_extinf_rounding_up_past_target_duration_is_reported():
    playlist = _replace(VALID_VOD_PLAYLIST, "#EXTINF:5.5,", "#EXTINF:6.5,")

    assert rules(validate_playlist(playlist)) == [RULE_EXTINF_EXCEEDS_TARGET_DURATION]


def test_target_duration_rule_is_skipped_when_tag_is_missing():
    playlist = _replace(VALID_VOD_PLAYLIST, "#EXT-X-TARGETDURATION:6\n", "")

    assert validate_playlist(playlist) == []


def test_byterange_without_offset_requires_previous_sub_range_of_same_uri():
    playlist = textwrap.dedent(
        """\
        #EXTM3U
        #EXT-X-VERSION:4
        #EXT-X-TARGETDURATION:10
        #EXT-X-PLAYLIST-TYPE:VOD
        #EXTINF:10,
        #EXT-X-BYTERANGE:1000
        same.ts
        #EXTINF:10,
        #EXT-X-BYTERANGE:1000
        same.ts
        #EXT-X-ENDLIST
        """
    )

    assert rules(validate_playlist(playlist)) == [RULE_BYTERANGE_OFFSET_REQUIRED]


def test_byterange_without_offset_after_other_resource_is_reported():
    playlist = textwrap.dedent(
        """\
        #EXTM3U
        #EXT-X-TARGETDURATION:10
        #EXT-X-PLAYLIST-TYPE:VOD
        #EXTINF:10,
        #EXT-X-BYTERANGE:1000@0
        first.ts
        #EXTINF:10,
        #EXT-X-BYTERANGE:1000
        second.ts
        #EXT-X-ENDLIST
        """
    )

    diagnostics = validate_playlist(playlist)

    assert rules(diagnostics) == [RULE_BYTERANGE_OFFSET_REQUIRED]
    assert diagnostics[0].segment_index == 1
    assert "second.ts" in diagnostics[0].message


def test_byterange_without_offset_after_whole_resource_is_reported():
    playlist = textwrap.dedent(
        """\
        #EXTM3U
        #EXT-X-TARGETDURATION:10
        #EXT-X-PLAYLIST-TYPE:VOD
        #EXTINF:10,
        segment.ts
        #EXTINF:10,
        #EXT-X-BYTERANGE:1000
        segment.ts
        #EXT-X-ENDLIST
        """
    )

    assert rules(validate_playlist(playlist)) == [RULE_BYTERANGE_OFFSET_REQUIRED]


def test_invalid_byterange_value_is_reported():
    playlist = _replace(
        MULTI_ERROR_PLAYLIST, "#EXT-X-BYTERANGE:900", "#EXT-X-BYTERANGE:abc"
    )

    assert RULE_BYTERANGE_INVALID in rules(validate_playlist(playlist))


def test_fmp4_segment_without_map_is_reported():
    playlist = textwrap.dedent(
        """\
        #EXTM3U
        #EXT-X-VERSION:7
        #EXT-X-TARGETDURATION:4
        #EXT-X-PLAYLIST-TYPE:VOD
        #EXTINF:4,
        a.m4s
        #EXT-X-ENDLIST
        """
    )

    diagnostics = validate_playlist(playlist)

    assert rules(diagnostics) == [RULE_FMP4_MAP_MISSING]
    assert diagnostics[0].line == 5
    assert diagnostics[0].segment_index == 0
    assert diagnostics[0].uri == "a.m4s"


def test_map_rule_does_not_apply_to_transport_stream_segments():
    assert validate_playlist(VALID_LIVE_PLAYLIST) == []


def test_ambiguous_mp4_without_map_is_a_warning_only_with_fmp4_intent():
    without_intent = textwrap.dedent(
        """\
        #EXTM3U
        #EXT-X-TARGETDURATION:4
        #EXT-X-PLAYLIST-TYPE:VOD
        #EXTINF:4,
        a.mp4
        #EXT-X-ENDLIST
        """
    )
    with_intent = _replace(without_intent, "#EXTM3U\n", "#EXTM3U\n#EXT-X-VERSION:7\n")

    assert validate_playlist(without_intent) == []

    diagnostics = validate_playlist(with_intent)
    assert rules(diagnostics) == [RULE_FMP4_MAP_MISSING]
    assert diagnostics[0].severity == "warning"


def test_map_applies_to_all_following_segments_until_replaced():
    playlist = textwrap.dedent(
        """\
        #EXTM3U
        #EXT-X-VERSION:7
        #EXT-X-TARGETDURATION:4
        #EXT-X-PLAYLIST-TYPE:VOD
        #EXT-X-MAP:URI="init.mp4"
        #EXTINF:4,
        a.m4s
        #EXTINF:4,
        b.m4s
        #EXT-X-ENDLIST
        """
    )

    assert validate_playlist(playlist) == []


def test_map_uri_is_required():
    playlist = textwrap.dedent(
        """\
        #EXTM3U
        #EXT-X-VERSION:7
        #EXT-X-TARGETDURATION:4
        #EXT-X-PLAYLIST-TYPE:VOD
        #EXT-X-MAP:BYTERANGE="100@0"
        #EXTINF:4,
        a.m4s
        #EXT-X-ENDLIST
        """
    )

    diagnostics = validate_playlist(playlist)

    # The URI-less MAP still applies, so only its own defect is reported.
    assert rules(diagnostics) == [RULE_MAP_URI_MISSING]
    assert diagnostics[0].line == 5
    assert diagnostics[0].tag == "#EXT-X-MAP"


def test_map_byterange_must_be_length_or_length_at_offset():
    playlist = textwrap.dedent(
        """\
        #EXTM3U
        #EXT-X-VERSION:7
        #EXT-X-TARGETDURATION:4
        #EXT-X-PLAYLIST-TYPE:VOD
        #EXT-X-MAP:URI="init.mp4",BYTERANGE="nope"
        #EXTINF:4,
        a.m4s
        #EXT-X-ENDLIST
        """
    )

    diagnostics = validate_playlist(playlist)

    assert rules(diagnostics) == [RULE_MAP_BYTERANGE_INVALID]
    assert diagnostics[0].line == 5


def test_vod_with_endlist_is_accepted():
    assert RULE_VOD_MISSING_ENDLIST not in rules(validate_playlist(VALID_VOD_PLAYLIST))


def test_event_playlist_without_endlist_is_accepted():
    playlist = textwrap.dedent(
        """\
        #EXTM3U
        #EXT-X-TARGETDURATION:8
        #EXT-X-PLAYLIST-TYPE:EVENT
        #EXTINF:8,
        segment1.ts
        #EXTINF:8,
        segment2.ts
        """
    )

    assert validate_playlist(playlist) == []


def test_master_playlist_produces_no_diagnostics():
    assert validate_playlist(playlists.VARIANT_PLAYLIST) == []


@pytest.mark.parametrize(
    "content", ["", "#EXTM3U\n", "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"]
)
def test_empty_media_playlists_produce_no_diagnostics(content):
    assert validate_playlist(content) == []


def test_m3u8_object_is_accepted_and_reports_locations():
    obj = m3u8.loads(MULTI_ERROR_PLAYLIST)

    assert rules(obj.validate()) == rules(validate_playlist(MULTI_ERROR_PLAYLIST))
    assert lines(obj.validate()) == lines(validate_playlist(MULTI_ERROR_PLAYLIST))


def test_validation_is_read_only():
    obj = m3u8.loads(MULTI_ERROR_PLAYLIST)
    content_before = obj._source_content
    uris_before = [segment.uri for segment in obj.segments]
    sequences_before = [segment.media_sequence for segment in obj.segments]
    dump_before = obj.dumps()

    obj.validate()

    assert obj._source_content == content_before
    assert [segment.uri for segment in obj.segments] == uris_before
    assert [segment.media_sequence for segment in obj.segments] == sequences_before
    assert obj.dumps() == dump_before


def test_validate_playlist_does_not_change_string_input_or_parsed_data():
    data = m3u8.parse(MULTI_ERROR_PLAYLIST)
    snapshot = repr(data)

    validate_playlist(MULTI_ERROR_PLAYLIST)

    assert repr(m3u8.parse(MULTI_ERROR_PLAYLIST)) == snapshot


def test_dump_roundtrip_keeps_same_diagnostics_and_positions():
    obj = m3u8.loads(MULTI_ERROR_PLAYLIST)
    original = validate_playlist(MULTI_ERROR_PLAYLIST)
    reparsed = validate_playlist(m3u8.loads(obj.dumps()))

    assert [(d.rule, d.line, d.segment_index) for d in reparsed] == [
        (d.rule, d.line, d.segment_index) for d in original
    ]


def test_saved_playlist_reloaded_from_disk_keeps_diagnostics(tmp_path):
    obj = m3u8.loads(MULTI_ERROR_PLAYLIST)
    filename = tmp_path / "playlist.m3u8"
    obj.dump(str(filename))

    original = validate_playlist(obj)
    reloaded = validate_playlist(m3u8.load(str(filename)))

    assert [(d.rule, d.line, d.segment_index) for d in reloaded] == [
        (d.rule, d.line, d.segment_index) for d in original
    ]


def test_saved_playlist_reloaded_from_string_object_keeps_diagnostics(tmp_path):
    obj = m3u8.loads(MULTI_ERROR_PLAYLIST)
    filename = tmp_path / "playlist.m3u8"
    obj.dump(str(filename))

    content = filename.read_text()
    assert validate_playlist(content) == validate_playlist(m3u8.loads(content))


def test_parse_and_dumps_behaviour_is_unchanged():
    obj = m3u8.loads(MULTI_ERROR_PLAYLIST)

    assert obj.dumps() == m3u8.M3U8(MULTI_ERROR_PLAYLIST).dumps()
    assert obj.media_sequence == 10
    assert [segment.media_sequence for segment in obj.segments] == [10, 11, 12, 13]


def test_diagnostic_as_dict_contains_all_fields():
    diagnostic = validate_playlist(MULTI_ERROR_PLAYLIST)[1]

    assert diagnostic.as_dict() == {
        "rule": RULE_EXTINF_EXCEEDS_TARGET_DURATION,
        "severity": "error",
        "message": diagnostic.message,
        "line": 7,
        "tag": "#EXTINF",
        "segment_index": 0,
        "media_sequence": 10,
        "uri": "seg1.m4s",
    }
    assert diagnostic.rule_id == RULE_EXTINF_EXCEEDS_TARGET_DURATION


def test_str_is_locatable():
    diagnostic = validate_playlist(MULTI_ERROR_PLAYLIST)[2]

    assert "line 9" in str(diagnostic)
    assert RULE_BYTERANGE_OFFSET_REQUIRED in str(diagnostic)


def test_every_emitted_rule_is_documented_with_a_severity():
    emitted = set()
    for playlist in (
        MULTI_ERROR_PLAYLIST,
        _replace(MULTI_ERROR_PLAYLIST, "#EXT-X-BYTERANGE:900", "#EXT-X-BYTERANGE:x"),
        _replace(
            MULTI_ERROR_PLAYLIST, '#EXT-X-MAP:URI="init.mp4"', "#EXT-X-MAP:BYTERANGE=x"
        ),
    ):
        emitted.update(rules(validate_playlist(playlist)))

    assert emitted <= set(RULES)
    for rule in emitted:
        assert RULES[rule]["severity"] in ("error", "warning")
        assert RULES[rule]["description"]


def test_diagnostic_objects_are_comparable_and_immutable():
    first = validate_playlist(MULTI_ERROR_PLAYLIST)
    second = validate_playlist(MULTI_ERROR_PLAYLIST)

    assert first == second
    with pytest.raises(dataclasses.FrozenInstanceError):
        first[0].line = 1  # type: ignore[misc]
    assert isinstance(first[0], Diagnostic)


def test_object_without_source_text_still_reports_segment_indexes():
    data = m3u8.parse(MULTI_ERROR_PLAYLIST)
    obj = m3u8.M3U8()
    obj.data = data
    obj._initialize_attributes()

    diagnostics = validate_playlist(obj)

    assert rules(diagnostics) == rules(validate_playlist(MULTI_ERROR_PLAYLIST))
    assert all(diagnostic.line is None for diagnostic in diagnostics)
    assert [d.segment_index for d in diagnostics if d.segment_index is not None] == [
        0,
        1,
        3,
    ]


def test_unsupported_input_type_is_rejected():
    with pytest.raises(TypeError):
        validate_playlist(42)


def _replace(content, old, new):
    assert old in content
    return content.replace(old, new, 1)
