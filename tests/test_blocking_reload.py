# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

# Tests for Low-Latency HLS blocking playlist reload request planning.
# Everything here is offline: playlists are parsed from strings and the HTTP
# layer is mocked, so no test server is required.

from urllib.parse import parse_qs, urlsplit

import playlists

import m3u8
from m3u8.reload import (
    REASON_BLOCKING_NOT_ALLOWED,
    REASON_EMPTY_PLAYLIST,
    REASON_ENDLIST,
    REASON_NO_NEXT_PART,
    REASON_VARIANT_PLAYLIST,
)

URI = "https://example.com/2M/waitForMSN.php?token=abc&origin=edge"

# A live, blocking-reload capable playlist whose last segment is still being
# written: three PART tags followed by a PART preload hint.
PART_PLAYLIST = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-TARGETDURATION:4
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0,CAN-SKIP-UNTIL=12.0
#EXT-X-PART-INF:PART-TARGET=0.33334
#EXT-X-MEDIA-SEQUENCE:271
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4.00008,
fileSequence271.mp4
#EXT-X-PART:DURATION=0.33334,URI="filePart272.0.mp4",INDEPENDENT=YES
#EXT-X-PART:DURATION=0.33334,URI="filePart272.1.mp4"
#EXT-X-PART:DURATION=0.33334,URI="filePart272.2.mp4"
#EXT-X-PRELOAD-HINT:TYPE=PART,URI="filePart272.3.mp4"
"""

# What the server returns once part 272.3 has been published: the preload hint
# moved on and a new PART appeared, so the client must advance instead of
# requesting part 3 again.
PART_PLAYLIST_REFRESHED = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-TARGETDURATION:4
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0,CAN-SKIP-UNTIL=12.0
#EXT-X-PART-INF:PART-TARGET=0.33334
#EXT-X-MEDIA-SEQUENCE:271
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4.00008,
fileSequence271.mp4
#EXT-X-PART:DURATION=0.33334,URI="filePart272.0.mp4",INDEPENDENT=YES
#EXT-X-PART:DURATION=0.33334,URI="filePart272.1.mp4"
#EXT-X-PART:DURATION=0.33334,URI="filePart272.2.mp4"
#EXT-X-PART:DURATION=0.33334,URI="filePart272.3.mp4"
#EXT-X-PRELOAD-HINT:TYPE=PART,URI="filePart272.4.mp4"
"""

# A playlist whose last item is a complete Media Segment (no trailing PARTs).
FULL_SEGMENT_PLAYLIST = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:10
#EXTINF:4.0,
seg10.ts
#EXTINF:4.0,
seg11.ts
"""

# Incomplete trailing segment with PART tags but no preload hint.
TRAILING_PART_PLAYLIST = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:40
#EXTINF:4.0,
seg40.ts
#EXT-X-PART:DURATION=0.33334,URI="part41.0.mp4",INDEPENDENT=YES
#EXT-X-PART:DURATION=0.33334,URI="part41.1.mp4"
"""

EMPTY_PLAYLIST = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-TARGETDURATION:4
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES
"""

MAP_HINT_PLAYLIST = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:7
#EXTINF:4.0,
seg7.ts
#EXT-X-PRELOAD-HINT:TYPE=MAP,URI="init.mp4"
"""

VARIANT_PLAYLIST = """\
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1280000,RESOLUTION="1280x720"
video.m3u8
"""


def _query(uri):
    return parse_qs(urlsplit(uri).query)


def _plan(content, *args, **kwargs):
    return m3u8.loads(content, uri=URI).plan_blocking_reload(URI, *args, **kwargs)


# ---------------------------------------------------------------------------
# Blocking request computation: _HLS_msn / _HLS_part
# ---------------------------------------------------------------------------


def test_preload_hint_targets_the_hinted_part_on_first_reload():
    request = _plan(PART_PLAYLIST, first_reload=True)

    assert request.is_blocking
    assert request.reason is None
    assert request.msn == 272
    assert request.part == 3
    query = _query(request.uri)
    # The trailing incomplete segment holds MSN 272 with three parts (0..2),
    # so the hinted part is index 3.
    assert query["_HLS_msn"] == ["272"]
    assert query["_HLS_part"] == ["3"]
    assert "_HLS_skip" not in query
    # Original query parameters are preserved verbatim.
    assert query["token"] == ["abc"]
    assert query["origin"] == ["edge"]


def test_first_reload_never_carries_skip_even_when_requested():
    request = _plan(PART_PLAYLIST, first_reload=True, skip="YES")

    assert request.is_blocking
    assert request.skip is None
    assert "_HLS_skip" not in _query(request.uri)


def test_skip_yes_is_forwarded_when_server_allows_skipping():
    request = _plan(PART_PLAYLIST, skip="YES")

    assert request.is_blocking
    assert request.skip == "YES"
    assert _query(request.uri)["_HLS_skip"] == ["YES"]


def test_skip_v2_requires_can_skip_dateranges_and_falls_back_to_yes():
    # PART_PLAYLIST advertises CAN-SKIP-UNTIL but not CAN-SKIP-DATERANGES.
    request = _plan(PART_PLAYLIST, skip="v2")

    assert request.skip == "YES"
    assert _query(request.uri)["_HLS_skip"] == ["YES"]


def test_skip_v2_is_forwarded_with_can_skip_dateranges():
    content = PART_PLAYLIST.replace(
        "CAN-SKIP-UNTIL=12.0",
        "CAN-SKIP-UNTIL=12.0,CAN-SKIP-DATERANGES=YES",
    )
    request = _plan(content, skip="v2")

    assert request.skip == "v2"
    assert _query(request.uri)["_HLS_skip"] == ["v2"]


def test_skip_is_dropped_without_can_skip_until():
    # The library fixture has CAN-BLOCK-RELOAD but no CAN-SKIP-UNTIL.
    obj = m3u8.loads(playlists.LOW_LATENCY_OMITTED_ATTRIBUTES, uri=URI)
    request = obj.plan_blocking_reload(URI, skip="YES")

    assert request.is_blocking
    assert request.skip is None
    assert "_HLS_skip" not in _query(request.uri)


def test_unknown_skip_value_is_normalized_to_yes():
    request = _plan(PART_PLAYLIST, skip="maybe")

    assert request.skip == "YES"
    assert _query(request.uri)["_HLS_skip"] == ["YES"]


def test_trailing_parts_without_hint_target_next_part_index():
    request = _plan(TRAILING_PART_PLAYLIST)

    assert request.is_blocking
    assert request.msn == 41
    assert request.part == 2
    query = _query(request.uri)
    assert query["_HLS_part"] == ["2"]


def test_full_segment_playlist_waits_for_part_zero_of_next_segment():
    request = _plan(FULL_SEGMENT_PLAYLIST)

    assert request.is_blocking
    assert request.msn == 12
    assert request.part == 0
    query = _query(request.uri)
    assert query["_HLS_msn"] == ["12"]
    assert query["_HLS_part"] == ["0"]


def test_map_preload_hint_is_not_a_part_target():
    # A MAP hint does not announce a Partial Segment, so the client waits for
    # the first PART of the next Media Segment.
    request = _plan(MAP_HINT_PLAYLIST)

    assert request.is_blocking
    assert request.msn == 8
    assert request.part == 0


def test_can_block_reload_matching_is_case_insensitive():
    content = PART_PLAYLIST.replace("CAN-BLOCK-RELOAD=YES", "CAN-BLOCK-RELOAD=yes")
    request = _plan(content)

    assert request.is_blocking


# ---------------------------------------------------------------------------
# Degraded / non-blocking outcomes
# ---------------------------------------------------------------------------


def test_blocking_disabled_degrades_to_plain_reload():
    content = PART_PLAYLIST.replace(
        "#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0,CAN-SKIP-UNTIL=12.0\n",
        "#EXT-X-SERVER-CONTROL:PART-HOLD-BACK=1.0\n",
    )
    request = _plan(content)

    assert not request.is_blocking
    assert request.reason == REASON_BLOCKING_NOT_ALLOWED
    assert request.uri == URI
    assert request.msn is None
    assert request.part is None
    assert "_HLS_msn" not in _query(request.uri)


def test_missing_server_control_degrades_to_plain_reload():
    content = PART_PLAYLIST.split("\n")
    content = "\n".join(
        line for line in content if not line.startswith("#EXT-X-SERVER-CONTROL")
    )
    request = _plan(content)

    assert not request.is_blocking
    assert request.reason == REASON_BLOCKING_NOT_ALLOWED
    assert request.uri == URI


def test_empty_playlist_has_an_explicit_result():
    request = _plan(EMPTY_PLAYLIST)

    assert not request.is_blocking
    assert request.reason == REASON_EMPTY_PLAYLIST
    assert request.uri == URI
    assert request.msn is None and request.part is None


def test_endlist_never_blocks():
    request = _plan(FULL_SEGMENT_PLAYLIST + "#EXT-X-ENDLIST\n")

    assert not request.is_blocking
    assert request.reason == REASON_ENDLIST
    assert request.uri == URI


def test_variant_playlist_is_not_reloaded_with_blocking():
    request = _plan(VARIANT_PLAYLIST)

    assert not request.is_blocking
    assert request.reason == REASON_VARIANT_PLAYLIST
    assert request.uri == URI


def test_reason_constants_cover_all_degraded_paths():
    # Guard against accidental renames of the public reason vocabulary.
    assert REASON_VARIANT_PLAYLIST == "variant-playlist"
    assert REASON_ENDLIST == "endlist"
    assert REASON_BLOCKING_NOT_ALLOWED == "blocking-reload-not-allowed"
    assert REASON_EMPTY_PLAYLIST == "empty-playlist"
    assert REASON_NO_NEXT_PART == "no-next-part"


# ---------------------------------------------------------------------------
# Sequence number overflow
# ---------------------------------------------------------------------------


def test_media_sequence_wraps_around_unsigned_64_bits():
    # The single, complete segment holds MSN 2**64 - 1, so the blocking
    # request must wrap to MSN 0.
    content = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:18446744073709551615
#EXTINF:4.0,
segMax.ts
"""
    request = _plan(content)

    assert request.is_blocking
    assert request.msn == 0
    assert request.part == 0
    assert _query(request.uri)["_HLS_msn"] == ["0"]


def test_trailing_part_msn_also_wraps():
    # The complete segment is MSN 2**64 - 2 and the incomplete trailing
    # segment therefore holds MSN 2**64 - 1, which wraps in the request.
    content = """\
#EXTM3U
#EXT-X-VERSION:9
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:18446744073709551614
#EXTINF:4.0,
segMaxMinusOne.ts
#EXT-X-PART:DURATION=0.33334,URI="partMax.0.mp4",INDEPENDENT=YES
#EXT-X-PART:DURATION=0.33334,URI="partMax.1.mp4"
"""
    request = _plan(content)

    assert request.is_blocking
    assert request.msn == 18446744073709551615 % (2**64)
    assert request.part == 2


# ---------------------------------------------------------------------------
# Two-round conversation with PART playlists: advancement without re-fetching
# already seen parts, including the offline loader round trip.
# ---------------------------------------------------------------------------


def test_two_round_part_playlist_advances_instead_of_re_requesting_part():
    playlist = m3u8.loads(PART_PLAYLIST, uri=URI)

    first = playlist.plan_blocking_reload(URI, first_reload=True)
    assert _query(first.uri)["_HLS_part"] == ["3"]
    assert first.msn == 272 and first.part == 3

    # Simulate the held-open response delivering part 3 and a new preload hint.
    refreshed = m3u8.loads(PART_PLAYLIST_REFRESHED, uri=URI)
    second = refreshed.plan_blocking_reload(URI, skip="YES")

    assert second.is_blocking
    assert second.msn == 272
    assert second.part == 4
    query = _query(second.uri)
    assert query["_HLS_part"] == ["4"]
    assert query["_HLS_skip"] == ["YES"]
    # The previous part must never be requested again.
    assert query["_HLS_part"] != ["3"]
    # Original query parameters survive every reload.
    assert query["token"] == ["abc"]
    assert query["origin"] == ["edge"]


def test_two_round_conversation_with_library_fixtures():
    # Same flow exercised against the LL-HLS fixtures shipped with the suite.
    first_playlist = m3u8.loads(playlists.LOW_LATENCY_PART_PLAYLIST, uri=URI)
    first = first_playlist.plan_blocking_reload(URI, first_reload=True)

    assert first.is_blocking
    assert first.msn == 273
    assert first.part == 3
    assert _query(first.uri)["_HLS_msn"] == ["273"]

    # The delta update is the response to a request for msn=273, part=3.
    delta = m3u8.loads(playlists.LOW_LATENCY_DELTA_UPDATE_PLAYLIST, uri=URI)
    second = delta.plan_blocking_reload(URI, skip="YES")

    assert second.is_blocking
    assert second.msn == 273
    assert second.part == 4
    query = _query(second.uri)
    assert query["_HLS_msn"] == ["273"]
    assert query["_HLS_part"] == ["4"]
    assert query["_HLS_skip"] == ["YES"]


def test_offline_loader_round_trip_uses_planned_uri_and_advances(
    monkeypatch,
):
    # Reproduces the original offline test style: m3u8.load is driven with a
    # mocked HTTP client, proving the planned URI is what actually gets
    # requested and the next plan advances with the refreshed playlist.
    responses = iter([PART_PLAYLIST, PART_PLAYLIST_REFRESHED])
    seen_uris = []

    class FakeHTTPClient:
        def download(self, uri, timeout, headers, verify_ssl):
            seen_uris.append(uri)
            return next(responses), "https://example.com/2M/"

    playlist = m3u8.load(URI, http_client=FakeHTTPClient())
    first = playlist.plan_blocking_reload(URI, first_reload=True)

    refreshed = m3u8.load(first.uri, http_client=FakeHTTPClient())
    second = refreshed.plan_blocking_reload(URI, skip="YES")

    assert seen_uris[0] == URI
    assert "_HLS_msn=272" in seen_uris[1] and "_HLS_part=3" in seen_uris[1]
    assert second.part == 4
    assert "_HLS_part=4" in second.uri


def test_fallback_window_when_server_drops_blocking_support():
    # If a refreshed playlist no longer advertises CAN-BLOCK-RELOAD the
    # client must fall back to a plain poll of the original URI.
    playlist = m3u8.loads(PART_PLAYLIST, uri=URI)
    first = playlist.plan_blocking_reload(URI, first_reload=True)
    assert first.is_blocking

    degraded = PART_PLAYLIST.replace(
        "#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0,CAN-SKIP-UNTIL=12.0\n",
        "#EXT-X-SERVER-CONTROL:PART-HOLD-BACK=1.0\n",
    )
    refreshed = m3u8.loads(degraded, uri=URI)
    fallback = refreshed.plan_blocking_reload(URI)

    assert not fallback.is_blocking
    assert fallback.reason == REASON_BLOCKING_NOT_ALLOWED
    assert fallback.uri == URI


def test_fallback_window_when_refreshed_playlist_becomes_empty():
    playlist = m3u8.loads(PART_PLAYLIST, uri=URI)
    assert playlist.plan_blocking_reload(URI).is_blocking

    refreshed = m3u8.loads(EMPTY_PLAYLIST, uri=URI)
    fallback = refreshed.plan_blocking_reload(URI)

    assert not fallback.is_blocking
    assert fallback.reason == REASON_EMPTY_PLAYLIST
    assert fallback.uri == URI


# ---------------------------------------------------------------------------
# URI handling
# ---------------------------------------------------------------------------


def test_uri_without_query_string_is_supported():
    uri = "https://example.com/plain.m3u8"
    request = m3u8.loads(PART_PLAYLIST, uri=uri).plan_blocking_reload(uri)

    assert urlsplit(request.uri).query.startswith("_HLS_msn=")


def test_fragment_is_preserved():
    uri = "https://example.com/waitForMSN.php?token=abc#frag"
    request = m3u8.loads(PART_PLAYLIST, uri=uri).plan_blocking_reload(uri)

    split = urlsplit(request.uri)
    assert split.fragment == "frag"
    assert split.query.endswith("_HLS_part=3")


def test_repeated_reloads_do_not_duplicate_hls_parameters():
    # Planning always starts from the original URI, so calling it repeatedly
    # must not stack _HLS_* parameters.
    playlist = m3u8.loads(PART_PLAYLIST, uri=URI)
    first = playlist.plan_blocking_reload(URI, first_reload=True)
    again = playlist.plan_blocking_reload(first.uri, skip="YES")

    query = _query(again.uri)
    assert query["_HLS_msn"] == ["272"]
    assert query["_HLS_part"] == ["3"]
    assert query["_HLS_skip"] == ["YES"]
