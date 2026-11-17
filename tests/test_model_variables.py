# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import playlists
import pytest

import m3u8
from m3u8.model import VariableDefinition
from m3u8.variables import (
    CyclicVariableReferenceError,
    DuplicateVariableError,
    IllegalSubstitutionValueError,
    InvalidVariableNameError,
    UndefinedVariableError,
    VariableDefinitionError,
)

# ---------------------------------------------------------------------------
# Master (Multivariant) playlist
# ---------------------------------------------------------------------------


def load_master():
    return m3u8.loads(playlists.VARIABLE_MASTER_PLAYLIST)


def test_master_exposes_terminal_variables():
    master = load_master()
    assert master.variables == {
        "CDN": "https://cdn.example.com",
        "VERSION": "v5",
        "REGION": "eu-west",
    }


def test_master_definitions_are_objects():
    master = load_master()
    assert master.defines == [
        VariableDefinition(name="CDN", value="https://cdn.example.com"),
        VariableDefinition(name="VERSION", value="v5"),
        VariableDefinition(name="REGION", value="eu-west"),
    ]


def test_master_variant_uris_are_raw_and_resolved():
    master = load_master()
    assert master.playlists[0].uri == ("{$CDN}/{$VERSION}/{$REGION}/720p/index.m3u8")
    assert master.playlists[0].resolved_uri == (
        "https://cdn.example.com/v5/eu-west/720p/index.m3u8"
    )
    assert master.resolved_playlist_uris == [
        "https://cdn.example.com/v5/eu-west/720p/index.m3u8",
        "https://cdn.example.com/v5/eu-west/1080p/index.m3u8",
    ]


def test_master_media_and_iframe_uris_resolve():
    master = load_master()
    assert master.media[0].resolved_uri == (
        "https://cdn.example.com/audio/en/index.m3u8"
    )
    assert master.media[1].resolved_uri == (
        "https://cdn.example.com/subs/en/index.m3u8"
    )
    assert master.iframe_playlists[0].resolved_uri == (
        "https://cdn.example.com/v5/eu-west/iframe/index.m3u8"
    )


def test_master_resolved_absolute_uri_uses_base_uri():
    master = m3u8.loads(
        playlists.VARIABLE_MASTER_PLAYLIST,
        uri="https://edge.example.com/hls/master.m3u8",
    )
    # Variant URIs already expand to absolute https URLs
    assert master.playlists[0].resolved_absolute_uri == (
        "https://cdn.example.com/v5/eu-west/720p/index.m3u8"
    )


# ---------------------------------------------------------------------------
# Media playlist: IMPORT from master + QUERYPARAM from the playlist URI
# ---------------------------------------------------------------------------


def load_media(master=None, uri=playlists.MEDIA_PLAYLIST_URI, **kwargs):
    master = master or load_master()
    return m3u8.loads(
        playlists.VARIABLE_MEDIA_PLAYLIST,
        uri=uri,
        variables=master.variables,
        **kwargs,
    )


def test_media_imports_master_variables():
    media = load_media()
    assert media.variables["CDN"] == "https://cdn.example.com"
    assert media.variables["VERSION"] == "v5"


def test_media_query_param_is_percent_decoded():
    media = load_media(uri="https://example.com/path/media.m3u8?REGION=us%20east")
    assert media.variables["REGION"] == "us east"


def test_media_local_definition_builds_on_imported_scope():
    media = load_media()
    assert media.variables["ASSET"] == ("https://cdn.example.com/v5/eu-west/720p")


def test_media_definition_kinds_are_preserved():
    media = load_media()
    assert media.defines[0] == VariableDefinition(import_="CDN")
    assert media.defines[1] == VariableDefinition(import_="VERSION")
    assert media.defines[2] == VariableDefinition(query_param="REGION")
    assert media.defines[3] == VariableDefinition(
        name="ASSET",
        value="{$CDN}/{$VERSION}/{$REGION}/720p",
    )


def test_media_segment_uris_raw_and_resolved():
    media = load_media()
    assert media.segments[0].uri == "{$ASSET}/segment-100.ts"
    assert media.segments[0].resolved_uri == (
        "https://cdn.example.com/v5/eu-west/720p/segment-100.ts"
    )
    assert media.resolved_segment_uris == [
        "https://cdn.example.com/v5/eu-west/720p/segment-100.ts",
        "https://cdn.example.com/v5/eu-west/720p/segment-101.ts",
        "https://cdn.example.com/v5/eu-west/720p/segment-102.ts",
    ]


def test_media_key_and_map_uris_resolve():
    media = load_media()
    assert media.keys[0].uri == "{$ASSET}/key.bin"
    assert media.keys[0].resolved_uri == (
        "https://cdn.example.com/v5/eu-west/720p/key.bin"
    )
    assert media.segment_map[0].resolved_uri == (
        "https://cdn.example.com/v5/eu-west/720p/init.mp4"
    )
    # the map attached to each segment resolves identically
    assert media.segments[0].init_section.resolved_uri == (
        "https://cdn.example.com/v5/eu-west/720p/init.mp4"
    )
    assert media.resolved_key_uris == [
        "https://cdn.example.com/v5/eu-west/720p/key.bin"
    ]


def test_expand_helper_uses_playlist_scope():
    media = load_media()
    assert media.expand("{$ASSET}/extra.bin") == (
        "https://cdn.example.com/v5/eu-west/720p/extra.bin"
    )


def test_media_loaded_without_query_parameter_fails():
    master = load_master()
    with pytest.raises(VariableDefinitionError, match="QUERYPARAM"):
        m3u8.loads(
            playlists.VARIABLE_MEDIA_PLAYLIST,
            uri="https://example.com/path/media.m3u8",
            variables=master.variables,
        )


def test_media_import_without_master_scope_fails():
    with pytest.raises(VariableDefinitionError, match="IMPORT"):
        m3u8.loads(
            playlists.VARIABLE_MEDIA_PLAYLIST,
            uri=playlists.MEDIA_PLAYLIST_URI,
        )


def test_explicit_query_variables_override_uri_query():
    media = load_media(query_variables={"REGION": "apac"})
    assert media.variables["REGION"] == "apac"
    assert media.segments[0].resolved_uri.startswith(
        "https://cdn.example.com/v5/apac/720p/"
    )


def test_explicit_variables_are_the_imported_scope():
    media = m3u8.loads(
        playlists.VARIABLE_MEDIA_PLAYLIST,
        uri=playlists.MEDIA_PLAYLIST_URI,
        variables={
            "CDN": "https://override.example",
            "VERSION": "v9",
        },
    )
    assert media.variables["CDN"] == "https://override.example"
    assert media.variables["VERSION"] == "v9"
    assert media.segments[0].resolved_uri.startswith(
        "https://override.example/v9/eu-west/720p/"
    )


def test_direct_constructor_reads_query_parameters_from_uri():
    content = (
        '#EXTM3U\n#EXT-X-DEFINE:QUERYPARAM="REGION"\n'
        '#EXT-X-DEFINE:NAME="P",VALUE="r-{$REGION}"\n'
        "#EXTINF:5,\n{$P}/seg.ts\n"
    )
    playlist = m3u8.M3U8(content, uri="https://h/p.m3u8?REGION=eu-west")
    assert playlist.variables == {"REGION": "eu-west", "P": "r-eu-west"}
    assert playlist.segments[0].resolved_uri == "r-eu-west/seg.ts"

    explicit = m3u8.M3U8(
        content,
        uri="https://h/p.m3u8?REGION=eu-west",
        query_variables={"REGION": "apac"},
    )
    assert explicit.variables["REGION"] == "apac"


def test_local_name_definition_overrides_imported_variable():
    content = playlists.VARIABLE_MEDIA_PLAYLIST.replace(
        '#EXT-X-DEFINE:IMPORT="CDN"',
        '#EXT-X-DEFINE:NAME="CDN",VALUE="https://local-cdn.example"',
    )
    media = m3u8.loads(
        content,
        uri=playlists.MEDIA_PLAYLIST_URI,
        variables=load_master().variables,
    )
    assert media.variables["CDN"] == "https://local-cdn.example"


# ---------------------------------------------------------------------------
# Serialization round trips: raw write semantics are preserved
# ---------------------------------------------------------------------------


def test_master_dumps_round_trip_preserves_definitions_and_uris():
    master = load_master()
    serialized = master.dumps()
    assert serialized.startswith(
        "#EXTM3U\n"
        '#EXT-X-DEFINE:NAME="CDN",VALUE="https://cdn.example.com"\n'
        '#EXT-X-DEFINE:NAME="VERSION",VALUE="v5"\n'
        '#EXT-X-DEFINE:NAME="REGION",VALUE="eu-west"\n'
    )
    reparsed = m3u8.loads(serialized)
    assert reparsed.variables == master.variables
    assert reparsed.resolved_playlist_uris == master.resolved_playlist_uris
    assert reparsed.iframe_playlists[0].resolved_uri == (
        master.iframe_playlists[0].resolved_uri
    )


def test_media_dumps_round_trip_preserves_raw_references():
    media = load_media()
    serialized = media.dumps()
    assert '#EXT-X-DEFINE:IMPORT="CDN"' in serialized
    assert '#EXT-X-DEFINE:IMPORT="VERSION"' in serialized
    assert '#EXT-X-DEFINE:QUERYPARAM="REGION"' in serialized
    assert (
        '#EXT-X-DEFINE:NAME="ASSET",VALUE="{$CDN}/{$VERSION}/{$REGION}/720p"'
    ) in serialized
    assert "{$ASSET}/segment-100.ts" in serialized
    assert 'URI="{$ASSET}/key.bin"' in serialized
    assert 'URI="{$ASSET}/init.mp4"' in serialized

    reparsed = m3u8.loads(
        serialized,
        uri=playlists.MEDIA_PLAYLIST_URI,
        variables=load_master().variables,
    )
    assert reparsed.variables == media.variables
    assert reparsed.resolved_segment_uris == media.resolved_segment_uris
    assert reparsed.keys[0].resolved_uri == media.keys[0].resolved_uri


def test_fixture_files_match_inline_playlist_strings():
    # The paired on-disk fixtures and the inline constants must agree.
    with open(playlists.VARIABLE_MASTER_PLAYLIST_FILENAME) as f:
        assert f.read() == playlists.VARIABLE_MASTER_PLAYLIST
    with open(playlists.VARIABLE_MEDIA_PLAYLIST_FILENAME) as f:
        assert f.read() == playlists.VARIABLE_MEDIA_PLAYLIST


def test_loading_media_fixture_from_file_needs_query_variables():
    master = load_master()
    # A file path has no query string, so QUERYPARAM cannot be satisfied
    # unless the values are supplied explicitly.
    with pytest.raises(VariableDefinitionError, match="QUERYPARAM"):
        m3u8.load(
            playlists.VARIABLE_MEDIA_PLAYLIST_FILENAME, variables=master.variables
        )

    media = m3u8.load(
        playlists.VARIABLE_MEDIA_PLAYLIST_FILENAME,
        variables=master.variables,
        query_variables={"REGION": "eu-west"},
    )
    assert media.variables["ASSET"] == ("https://cdn.example.com/v5/eu-west/720p")
    assert media.resolved_segment_uris[0].endswith("segment-100.ts")


# ---------------------------------------------------------------------------
# Error matrix at the model layer
# ---------------------------------------------------------------------------


def test_undefined_variable_in_segment_raises():
    content = """#EXTM3U
#EXT-X-DEFINE:NAME="A",VALUE="https://a.example"
#EXTINF:5,
{$MISSING}/seg.ts
"""
    with pytest.raises(UndefinedVariableError):
        m3u8.loads(content)


def test_undefined_variable_in_key_uri_raises():
    content = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="{$MISSING}/key.bin"
#EXTINF:5,
seg.ts
"""
    with pytest.raises(UndefinedVariableError):
        m3u8.loads(content)


def test_cyclic_definition_raises_at_construction():
    content = '#EXTM3U\n#EXT-X-DEFINE:NAME="A",VALUE="{$A}"\n#EXTINF:5,\nseg.ts\n'
    with pytest.raises(CyclicVariableReferenceError):
        m3u8.loads(content)


def test_duplicate_definition_raises():
    content = (
        "#EXTM3U\n"
        '#EXT-X-DEFINE:NAME="A",VALUE="1"\n'
        '#EXT-X-DEFINE:NAME="A",VALUE="2"\n'
        "#EXTINF:5,\nseg.ts\n"
    )
    with pytest.raises(DuplicateVariableError):
        m3u8.loads(content)


def test_illegal_variable_name_raises():
    content = '#EXTM3U\n#EXT-X-DEFINE:NAME="_A",VALUE="1"\n#EXTINF:5,\nseg.ts\n'
    with pytest.raises(InvalidVariableNameError):
        m3u8.loads(content)


def test_malformed_reference_in_uri_raises():
    content = (
        '#EXTM3U\n#EXT-X-DEFINE:NAME="A",VALUE="https://a.example"\n'
        "#EXTINF:5,\n{$A/seg.ts\n"
    )
    with pytest.raises(IllegalSubstitutionValueError):
        m3u8.loads(content)


def test_value_with_double_quote_raises():
    content = '#EXTM3U\n#EXT-X-DEFINE:NAME="A",VALUE=\'a"b\'\n#EXTINF:5,\n{$A}/seg.ts\n'
    with pytest.raises(IllegalSubstitutionValueError):
        m3u8.loads(content)


def test_playlist_without_variables_is_unaffected():
    playlist = m3u8.loads("#EXTM3U\n#EXTINF:5,\nhttp://example.com/a.ts\n")
    assert playlist.variables == {}
    assert playlist.defines == []
    assert playlist.segments[0].resolved_uri == "http://example.com/a.ts"


def test_add_helpers_wire_the_playlist_scope():
    playlist = m3u8.loads('#EXTM3U\n#EXT-X-DEFINE:NAME="A",VALUE="https://a.example"\n')
    segment = m3u8.Segment(uri="{$A}/seg.ts", base_uri=None, duration=5)
    playlist.add_segment(segment)
    assert segment.resolved_uri == "https://a.example/seg.ts"

    variant = m3u8.Playlist(
        uri="{$A}/v.m3u8",
        stream_info={"bandwidth": 1},
        media=[],
        base_uri=None,
    )
    playlist.add_playlist(variant)
    assert variant.resolved_uri == "https://a.example/v.m3u8"


def test_files_keeps_raw_uris_while_resolved_lists_expand():
    playlist = m3u8.loads(
        '#EXTM3U\n#EXT-X-DEFINE:NAME="A",VALUE="https://a.example"\n'
        '#EXT-X-KEY:METHOD=AES-128,URI="{$A}/key.bin"\n'
        "#EXTINF:5,\n{$A}/seg.ts\n"
    )
    # files preserves the original, unexpanded references
    assert playlist.files == ["{$A}/key.bin", "{$A}/seg.ts"]
    # resolved accessors return the concrete URIs instead
    assert playlist.resolved_key_uris == ["https://a.example/key.bin"]
    assert playlist.resolved_segment_uris == ["https://a.example/seg.ts"]


# ---------------------------------------------------------------------------
# Partial segments, preload hints, rendition reports, session keys
# ---------------------------------------------------------------------------


def test_low_latency_uris_resolve():
    content = """#EXTM3U
#EXT-X-DEFINE:NAME="H",VALUE="https://h.example"
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0
#EXT-X-PART-INF:PART-TARGET=0.5
#EXT-X-TARGETDURATION:2
#EXT-X-MAP:URI="{$H}/init.mp4"
#EXT-X-PART:DURATION=0.5,URI="{$H}/p1.m4s"
#EXT-X-PART:DURATION=0.5,URI="{$H}/p2.m4s"
#EXT-X-PRELOAD-HINT:TYPE=PART,URI="{$H}/p3.m4s"
#EXT-X-RENDITION-REPORT:URI="{$H}/rendition.m3u8",LAST-MSN=10
#EXTINF:2,
{$H}/seg1.ts
"""
    playlist = m3u8.loads(content)
    assert playlist.segments[0].parts[0].resolved_uri == ("https://h.example/p1.m4s")
    assert playlist.segments[0].parts[1].resolved_uri == ("https://h.example/p2.m4s")
    assert playlist.preload_hint.resolved_uri == "https://h.example/p3.m4s"
    assert playlist.rendition_reports[0].resolved_uri == (
        "https://h.example/rendition.m3u8"
    )
    # round trip keeps the raw part URIs
    reparsed = m3u8.loads(playlist.dumps())
    assert reparsed.segments[0].parts[0].uri == "{$H}/p1.m4s"
    assert reparsed.preload_hint.uri == "{$H}/p3.m4s"


def test_session_key_and_content_steering_uris_resolve():
    content = """#EXTM3U
#EXT-X-DEFINE:NAME="S",VALUE="https://s.example"
#EXT-X-SESSION-KEY:METHOD=AES-128,URI="{$S}/session-key.bin"
#EXT-X-CONTENT-STEERING:SERVER-URI="{$S}/steering.json",PATHWAY-ID="main"
#EXT-X-STREAM-INF:BANDWIDTH=1000
{$S}/variant.m3u8
"""
    playlist = m3u8.loads(content)
    assert playlist.session_keys[0].resolved_uri == (
        "https://s.example/session-key.bin"
    )
    assert playlist.content_steering.resolved_uri == ("https://s.example/steering.json")
