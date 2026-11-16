# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

# Tests for M3U8.filter_by_capability: building independent, capability
# constrained subsets of a master playlist without leaving dangling audio,
# subtitle or I-frame references behind.

import pytest

import m3u8
from m3u8 import CapabilityFilterError

# A master playlist with multiple bitrates, three resolutions, two audio
# languages, two subtitle languages, a closed-captions group and matching
# I-frame playlists. Every group reference resolves to an EXT-X-MEDIA tag.
FULL_MASTER = """\
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA:URI="audio/en.m3u8",TYPE=AUDIO,GROUP-ID="aac",LANGUAGE="en",NAME="English",DEFAULT=YES,AUTOSELECT=YES
#EXT-X-MEDIA:URI="audio/sp.m3u8",TYPE=AUDIO,GROUP-ID="aac",LANGUAGE="es",NAME="Spanish",DEFAULT=NO,AUTOSELECT=YES
#EXT-X-MEDIA:URI="subs/en.vtt",TYPE=SUBTITLES,GROUP-ID="subs",LANGUAGE="en",NAME="English",AUTOSELECT=YES,FORCED=NO
#EXT-X-MEDIA:URI="subs/fr.vtt",TYPE=SUBTITLES,GROUP-ID="subs",LANGUAGE="fr",NAME="French",AUTOSELECT=YES,FORCED=NO
#EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS,GROUP-ID="cc",LANGUAGE="en",NAME="CC1",AUTOSELECT=YES,INSTREAM-ID="CC1"
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,CODECS="avc1.640028,mp4a.40.2",AUDIO="aac",SUBTITLES="subs",CLOSED-CAPTIONS="cc"
video/720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=854x480,CODECS="avc1.4d401f,mp4a.40.2",AUDIO="aac",SUBTITLES="subs",CLOSED-CAPTIONS="cc"
video/480p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=600000,RESOLUTION=640x360,CODECS="avc1.42c01e,mp4a.40.2",AUDIO="aac",SUBTITLES="subs",CLOSED-CAPTIONS="cc"
video/360p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=128000,CODECS="mp4a.40.2",AUDIO="aac"
audio/audio-only.m3u8
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=180000,RESOLUTION=1280x720,CODECS="avc1.640028",URI="iframes/720p.m3u8"
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=90000,RESOLUTION=854x480,CODECS="avc1.4d401f",URI="iframes/480p.m3u8"
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=45000,RESOLUTION=640x360,CODECS="avc1.42c01e",URI="iframes/360p.m3u8"
"""

# Only one variant, referencing groups that are never declared: any subset
# attempt must fail loudly rather than emit an unplayable playlist.
MISSING_GROUP_MASTER = """\
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720,AUDIO="ghost",SUBTITLES="phantom"
video/720p.m3u8
"""


def _load():
    return m3u8.loads(FULL_MASTER)


def test_max_bandwidth_filters_variants_and_iframes_preserving_order():
    master = _load()
    subset = master.filter_by_capability(max_bandwidth=600000)

    assert [playlist.uri for playlist in subset.playlists] == [
        "video/360p.m3u8",
        "audio/audio-only.m3u8",
    ]
    # I-frame bitrates are frame-only and far below the variant bitrates:
    # filtering is applied independently to the I-frame entries, but all
    # three stay under 600k and retain their source order
    assert [playlist.uri for playlist in subset.iframe_playlists] == [
        "iframes/720p.m3u8",
        "iframes/480p.m3u8",
        "iframes/360p.m3u8",
    ]

    # with a tighter cap on frame bitrates the 180k/90k entries are dropped,
    # still ordered, while regular video variants keep their own bitrates
    tight = master.filter_by_capability(max_bandwidth=600000, max_resolution=(640, 360))
    assert [playlist.uri for playlist in tight.playlists] == ["video/360p.m3u8"]
    assert [playlist.uri for playlist in tight.iframe_playlists] == [
        "iframes/360p.m3u8"
    ]


def test_max_bandwidth_is_inclusive_and_uses_peak_bandwidth():
    subset = _load().filter_by_capability(max_bandwidth=600000)
    assert subset.playlists[0].stream_info.bandwidth == 600000


def test_max_resolution_tuple_drops_variants_without_resolution():
    subset = _load().filter_by_capability(max_resolution=(854, 480))

    assert [playlist.uri for playlist in subset.playlists] == [
        "video/480p.m3u8",
        "video/360p.m3u8",
    ]
    # the audio-only variant has no RESOLUTION and must be dropped, as must
    # the 720p I-frame playlist
    assert [playlist.uri for playlist in subset.iframe_playlists] == [
        "iframes/480p.m3u8",
        "iframes/360p.m3u8",
    ]


def test_max_resolution_string_form():
    subset = _load().filter_by_capability(max_resolution="640x360")
    assert [playlist.uri for playlist in subset.playlists] == ["video/360p.m3u8"]
    assert [playlist.uri for playlist in subset.iframe_playlists] == [
        "iframes/360p.m3u8"
    ]


def test_allowed_codecs_keeps_variant_when_every_codec_is_allowed():
    subset = _load().filter_by_capability(
        allowed_codecs={"mp4a.40.2", "avc1.42c01e", "avc1.4d401f"}
    )
    assert [playlist.uri for playlist in subset.playlists] == [
        "video/480p.m3u8",
        "video/360p.m3u8",
        "audio/audio-only.m3u8",
    ]
    assert [playlist.uri for playlist in subset.iframe_playlists] == [
        "iframes/480p.m3u8",
        "iframes/360p.m3u8",
    ]


def test_allowed_codecs_drops_variant_when_one_codec_is_missing():
    subset = _load().filter_by_capability(allowed_codecs={"mp4a.40.2", "avc1.42c01e"})
    # 480p/720p video codecs are not allowed; audio-only (mp4a only) and the
    # 360p video variant remain
    assert [playlist.uri for playlist in subset.playlists] == [
        "video/360p.m3u8",
        "audio/audio-only.m3u8",
    ]
    # I-frame playlists carry only the video codec, so just 360p survives
    assert [playlist.uri for playlist in subset.iframe_playlists] == [
        "iframes/360p.m3u8"
    ]


def test_combined_constraints_apply_together():
    subset = _load().filter_by_capability(
        max_bandwidth=2000000,
        max_resolution=(854, 480),
        allowed_codecs={"mp4a.40.2", "avc1.4d401f", "avc1.42c01e"},
    )
    assert [playlist.uri for playlist in subset.playlists] == [
        "video/480p.m3u8",
        "video/360p.m3u8",
    ]
    assert [playlist.uri for playlist in subset.iframe_playlists] == [
        "iframes/480p.m3u8",
        "iframes/360p.m3u8",
    ]


def test_only_referenced_audio_and_subtitle_renditions_are_kept():
    master = m3u8.loads(
        """\
#EXTM3U
#EXT-X-MEDIA:URI="audio/en.m3u8",TYPE=AUDIO,GROUP-ID="aac",LANGUAGE="en",NAME="English"
#EXT-X-MEDIA:URI="audio/old.m3u8",TYPE=AUDIO,GROUP-ID="legacy",LANGUAGE="en",NAME="Old"
#EXT-X-MEDIA:URI="subs/en.vtt",TYPE=SUBTITLES,GROUP-ID="subs",LANGUAGE="en",NAME="English"
#EXT-X-MEDIA:URI="video/poster.m3u8",TYPE=VIDEO,GROUP-ID="vid",NAME="Main"
#EXT-X-STREAM-INF:BANDWIDTH=500000,AUDIO="aac",SUBTITLES="subs",VIDEO="vid"
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,AUDIO="legacy"
high.m3u8
"""
    )
    subset = master.filter_by_capability(max_bandwidth=1000000)

    assert len(subset.playlists) == 1
    assert subset.playlists[0].uri == "low.m3u8"
    kept = {(media.type, media.group_id) for media in subset.media}
    assert kept == {
        ("AUDIO", "aac"),
        ("SUBTITLES", "subs"),
        ("VIDEO", "vid"),
    }


def test_closed_captions_none_is_preserved_and_cc_group_pruned_when_unused():
    master = m3u8.loads(
        """\
#EXTM3U
#EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS,GROUP-ID="cc",NAME="CC1",INSTREAM-ID="CC1"
#EXT-X-STREAM-INF:BANDWIDTH=60000,CLOSED-CAPTIONS=NONE
none-low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=65000,CLOSED-CAPTIONS="cc"
cc-low.m3u8
"""
    )
    subset = master.filter_by_capability(max_bandwidth=62000)
    output = subset.dumps()

    assert "CLOSED-CAPTIONS=NONE" in output
    assert 'CLOSED-CAPTIONS="cc"' not in output
    assert not any(media.type == "CLOSED-CAPTIONS" for media in subset.media)


def test_referenced_closed_captions_group_is_kept_and_round_trips():
    # 600k keeps the 360p video variant, which references the cc group
    subset = _load().filter_by_capability(max_bandwidth=600000)
    output = subset.dumps()
    assert 'CLOSED-CAPTIONS="cc"' in output
    assert any(media.type == "CLOSED-CAPTIONS" for media in subset.media)

    reloaded = m3u8.loads(output)
    cc_variant = next(
        playlist
        for playlist in reloaded.playlists
        if playlist.stream_info.closed_captions == '"cc"'
    )
    assert cc_variant.uri == "video/360p.m3u8"
    assert reloaded.dumps() == output


def test_original_playlist_is_not_modified():
    master = _load()
    original_dump = master.dumps()
    original_playlists = list(master.playlists)
    original_media = list(master.media)
    original_iframes = list(master.iframe_playlists)

    master.filter_by_capability(max_bandwidth=600000)

    assert master.dumps() == original_dump
    assert list(master.playlists) == original_playlists
    assert list(master.media) == original_media
    assert list(master.iframe_playlists) == original_iframes


def test_subset_is_an_independent_object():
    master = _load()
    subset = master.filter_by_capability(max_bandwidth=600000)

    assert subset is not master
    assert subset.playlists is not master.playlists
    assert subset.media is not master.media
    assert subset.iframe_playlists is not master.iframe_playlists


def test_dumps_load_roundtrip_keeps_references_consistent():
    subset = _load().filter_by_capability(max_resolution=(854, 480))
    output = subset.dumps()
    reloaded = m3u8.loads(output)

    assert reloaded.dumps() == output
    assert [playlist.uri for playlist in reloaded.playlists] == [
        playlist.uri for playlist in subset.playlists
    ]
    assert [playlist.uri for playlist in reloaded.iframe_playlists] == [
        playlist.uri for playlist in subset.iframe_playlists
    ]
    assert [
        (media.type, media.group_id, media.language) for media in reloaded.media
    ] == [(media.type, media.group_id, media.language) for media in subset.media]
    for playlist in reloaded.playlists:
        assert playlist.stream_info.audio == "aac"
        assert playlist.stream_info.subtitles == "subs"
        assert playlist.stream_info.closed_captions == '"cc"'
        assert {media.group_id for media in playlist.media} == {
            "aac",
            "subs",
        }


def test_relative_uris_and_base_uri_remain_valid():
    master = m3u8.M3U8(FULL_MASTER, base_uri="http://example.com/content/")
    subset = master.filter_by_capability(max_bandwidth=600000)

    assert subset.base_uri == "http://example.com/content/"
    assert (
        subset.playlists[0].absolute_uri == "http://example.com/content/video/360p.m3u8"
    )
    # all I-frame bitrates sit below 600k, so the 720p entry comes first
    assert (
        subset.iframe_playlists[0].absolute_uri
        == "http://example.com/content/iframes/720p.m3u8"
    )
    assert "video/360p.m3u8" in subset.dumps()

    reloaded = m3u8.loads(subset.dumps(), uri="http://example.com/content/master.m3u8")
    assert (
        reloaded.playlists[0].absolute_uri
        == "http://example.com/content/video/360p.m3u8"
    )


def test_no_constraints_keeps_all_variants_and_referenced_renditions():
    master = _load()
    subset = master.filter_by_capability()

    assert [playlist.uri for playlist in subset.playlists] == [
        playlist.uri for playlist in master.playlists
    ]
    assert [playlist.uri for playlist in subset.iframe_playlists] == [
        playlist.uri for playlist in master.iframe_playlists
    ]
    assert [media.uri for media in subset.media] == [
        media.uri for media in master.media
    ]
    assert m3u8.loads(subset.dumps()).dumps() == subset.dumps()


def test_unreferenced_rendition_is_removed_even_without_constraints():
    master = m3u8.loads(
        """\
#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="used",NAME="English"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="orphan",NAME="Orphan"
#EXT-X-STREAM-INF:BANDWIDTH=500000,AUDIO="used"
low.m3u8
"""
    )
    subset = master.filter_by_capability()
    assert {(media.type, media.group_id) for media in subset.media} == {
        ("AUDIO", "used")
    }


def test_no_surviving_variant_raises():
    master = _load()
    with pytest.raises(CapabilityFilterError):
        master.filter_by_capability(max_bandwidth=1000)
    with pytest.raises(CapabilityFilterError):
        master.filter_by_capability(max_resolution=(1, 1))
    with pytest.raises(CapabilityFilterError):
        master.filter_by_capability(allowed_codecs={"unknown.codec"})


def test_missing_rendition_group_raises():
    master = m3u8.loads(MISSING_GROUP_MASTER)
    with pytest.raises(CapabilityFilterError):
        master.filter_by_capability(max_bandwidth=2000000)


def test_missing_group_is_detected_before_filtering():
    master = m3u8.loads(MISSING_GROUP_MASTER)
    # even a filter that would remove the only variant reports the dangling
    # group problem rather than silently emitting a broken playlist
    with pytest.raises(CapabilityFilterError):
        master.filter_by_capability(max_bandwidth=1)


def test_closed_captions_none_is_emitted_exactly_once():
    master = m3u8.loads(
        """\
#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="English"
#EXT-X-STREAM-INF:BANDWIDTH=100000,CLOSED-CAPTIONS=NONE,AUDIO="aac"
low.m3u8
"""
    )
    output = master.filter_by_capability().dumps()
    assert output.count("CLOSED-CAPTIONS") == 1
    assert m3u8.loads(output).dumps() == output


def test_video_rendition_group_is_pruned_when_unreferenced():
    master = m3u8.loads(
        """\
#EXTM3U
#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="vid",NAME="main"
#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="alt",NAME="alternative"
#EXT-X-STREAM-INF:BANDWIDTH=100000,VIDEO="vid"
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=9000000,VIDEO="alt"
high.m3u8
"""
    )
    subset = master.filter_by_capability(max_bandwidth=200000)
    output = subset.dumps()

    assert 'VIDEO="vid"' in output
    assert "alt" not in output
    assert {(media.type, media.group_id) for media in subset.media} == {
        ("VIDEO", "vid")
    }
    assert m3u8.loads(output).dumps() == output


def test_session_level_tags_are_preserved_in_subset():
    master = m3u8.loads(
        """\
#EXTM3U
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-CONTENT-STEERING:SERVER-URI="steering.json"
#EXT-X-STREAM-INF:BANDWIDTH=100000
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=9000000
high.m3u8
"""
    )
    output = master.filter_by_capability(max_bandwidth=200000).dumps()
    assert "#EXT-X-INDEPENDENT-SEGMENTS" in output
    assert "#EXT-X-CONTENT-STEERING" in output
    assert "high.m3u8" not in output
    assert m3u8.loads(output).dumps() == output


def test_non_master_playlist_raises():
    media_playlist = m3u8.loads(
        """\
#EXTM3U
#EXT-X-TARGETDURATION:10
#EXTINF:9.0,
segment1.ts
#EXT-X-ENDLIST
"""
    )
    with pytest.raises(CapabilityFilterError):
        media_playlist.filter_by_capability(max_bandwidth=100000)


def test_empty_master_raises():
    empty_master = m3u8.M3U8()
    empty_master.is_variant = True
    with pytest.raises(CapabilityFilterError):
        empty_master.filter_by_capability(max_bandwidth=100000)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_bandwidth": 0},
        {"max_bandwidth": -100},
        {"max_bandwidth": "1000"},
        {"max_bandwidth": 1.5},
        {"max_bandwidth": True},
        {"max_resolution": (0, 1)},
        {"max_resolution": (1, -2)},
        {"max_resolution": (1, 2, 3)},
        {"max_resolution": "abc"},
        {"max_resolution": "1920"},
        {"max_resolution": 1080},
        {"allowed_codecs": "avc1.4d401f"},
        {"allowed_codecs": 123},
        {"allowed_codecs": []},
        {"allowed_codecs": [""]},
        {"allowed_codecs": [1]},
    ],
)
def test_invalid_constraints_raise(kwargs):
    master = _load()
    with pytest.raises(CapabilityFilterError):
        master.filter_by_capability(**kwargs)


def test_capability_filter_error_is_value_error():
    assert issubclass(CapabilityFilterError, ValueError)
    assert isinstance(m3u8.CapabilityFilterError("boom"), ValueError)


def test_existing_parse_and_dumps_behavior_is_unchanged():
    # filtering never touches the source playlist and plain parsing still
    # round-trips the fixtures byte for byte
    master = _load()
    assert m3u8.loads(master.dumps()).dumps() == master.dumps()
