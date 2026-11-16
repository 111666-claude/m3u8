# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import playlists
import pytest

import m3u8
from m3u8.rendition import RenditionTrack

VIDEO_PLAYLIST = """\
#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-VERSION:9
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.0
#EXT-X-PART-INF:PART-TARGET=0.33334
#EXT-X-MEDIA-SEQUENCE:270
#EXT-X-KEY:METHOD=AES-128,URI="key.bin"
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4.00008,
fileSequence270.mp4
#EXTINF:4.00008,
fileSequence271.mp4
#EXT-X-PART:DURATION=0.33334,URI="filePart272.0.mp4",INDEPENDENT=YES
#EXT-X-PART:DURATION=0.33334,URI="filePart272.1.mp4"
#EXT-X-PART:DURATION=0.33334,URI="filePart272.2.mp4"
#EXT-X-PRELOAD-HINT:TYPE=PART,URI="filePart272.3.mp4"
#EXT-X-RENDITION-REPORT:URI="stale.m3u8",LAST-MSN=1,LAST-PART=0
"""

AUDIO_PLAYLIST = """\
#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-VERSION:9
#EXT-X-MEDIA-SEQUENCE:268
#EXTINF:4.00008,
audioSequence268.aac
#EXTINF:4.00008,
audioSequence269.aac
#EXTINF:4.00008,
audioSequence270.aac
#EXTINF:4.00008,
audioSequence271.aac
"""

AUDIO_PLAYLIST_ROLLED = """\
#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-VERSION:9
#EXT-X-MEDIA-SEQUENCE:270
#EXTINF:4.00008,
audioSequence270.aac
#EXTINF:4.00008,
audioSequence271.aac
#EXTINF:4.00008,
audioSequence272.aac
#EXTINF:4.00008,
audioSequence273.aac
"""

SUBTITLES_PLAYLIST = """\
#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:100
#EXTINF:4.0,
sub100.vtt
#EXTINF:4.0,
sub101.vtt
"""


def reports_of(playlist):
    return [
        (report.uri, report.last_msn, report.last_part)
        for report in playlist.rendition_reports
    ]


def test_two_tracks_get_mutual_relative_reports():
    video = m3u8.loads(VIDEO_PLAYLIST)
    audio = m3u8.loads(AUDIO_PLAYLIST)

    result = m3u8.generate_rendition_reports(
        {"video/2M/index.m3u8": video, "audio/en/index.m3u8": audio}
    )

    assert list(result) == ["video/2M/index.m3u8", "audio/en/index.m3u8"]
    assert result["video/2M/index.m3u8"] is video

    assert reports_of(video) == [("../../audio/en/index.m3u8", 271, None)]
    assert reports_of(audio) == [("../../video/2M/index.m3u8", 272, 2)]


def test_reports_are_computed_from_trailing_partial_segments():
    video = m3u8.loads(VIDEO_PLAYLIST)
    audio = m3u8.loads(playlists.LOW_LATENCY_DELTA_UPDATE_PLAYLIST)

    m3u8.generate_rendition_reports(
        [("video/index.m3u8", video), ("audio/index.m3u8", audio)]
    )

    # The video playlist ends with a partial segment: LAST-MSN is the MSN
    # of that segment and LAST-PART its Part Index.
    assert reports_of(audio) == [("../video/index.m3u8", 272, 2)]
    # The audio playlist is a delta update: the three skipped segments come
    # before the five listed ones, so its last MSN is 266 + 3 + 5 - 1.
    assert reports_of(video) == [("../audio/index.m3u8", 273, 3)]


def test_complete_playlists_get_no_last_part():
    audio = m3u8.loads(AUDIO_PLAYLIST)
    subtitles = m3u8.loads(SUBTITLES_PLAYLIST)

    m3u8.generate_rendition_reports(
        [("audio/index.m3u8", audio), ("subs/index.m3u8", subtitles)]
    )

    assert reports_of(audio) == [("../subs/index.m3u8", 101, None)]
    assert "LAST-PART" not in audio.dumps()
    assert m3u8.loads(audio.dumps()).rendition_reports[0].last_part is None


def test_three_tracks_and_rolling_windows():
    video = m3u8.loads(VIDEO_PLAYLIST)
    audio = m3u8.loads(AUDIO_PLAYLIST)
    subtitles = m3u8.loads(SUBTITLES_PLAYLIST)

    m3u8.generate_rendition_reports(
        [
            ("video/index.m3u8", video),
            ("audio/index.m3u8", audio),
            ("subs/index.m3u8", subtitles),
        ]
    )

    assert reports_of(video) == [
        ("../audio/index.m3u8", 271, None),
        ("../subs/index.m3u8", 101, None),
    ]
    assert reports_of(audio) == [
        ("../video/index.m3u8", 272, 2),
        ("../subs/index.m3u8", 101, None),
    ]
    assert reports_of(subtitles) == [
        ("../video/index.m3u8", 272, 2),
        ("../audio/index.m3u8", 271, None),
    ]

    # A newer snapshot of the audio track rolls its window forward by two
    # segments. Regenerating replaces the previous reports everywhere.
    audio = m3u8.loads(AUDIO_PLAYLIST_ROLLED)
    m3u8.generate_rendition_reports(
        [
            ("video/index.m3u8", video),
            ("audio/index.m3u8", audio),
            ("subs/index.m3u8", subtitles),
        ]
    )

    assert reports_of(video) == [
        ("../audio/index.m3u8", 273, None),
        ("../subs/index.m3u8", 101, None),
    ]
    assert len(video.rendition_reports) == 2
    assert reports_of(subtitles) == [
        ("../video/index.m3u8", 272, 2),
        ("../audio/index.m3u8", 273, None),
    ]


def test_generated_reports_survive_dump_and_load():
    video = m3u8.loads(VIDEO_PLAYLIST)
    audio = m3u8.loads(AUDIO_PLAYLIST)
    subtitles = m3u8.loads(SUBTITLES_PLAYLIST)

    m3u8.generate_rendition_reports(
        [
            ("video/index.m3u8", video),
            ("audio/index.m3u8", audio),
            ("subs/index.m3u8", subtitles),
        ]
    )

    for playlist in (video, audio, subtitles):
        reloaded = m3u8.loads(playlist.dumps())
        assert reports_of(reloaded) == reports_of(playlist)
        assert [segment.uri for segment in reloaded.segments] == [
            segment.uri for segment in playlist.segments
        ]
        assert len(reloaded.segments[-1].parts) == len(playlist.segments[-1].parts)
        assert reloaded.dumps() == playlist.dumps()


def test_reports_replace_stale_tags_and_keep_playlist_content():
    video = m3u8.loads(VIDEO_PLAYLIST)
    audio = m3u8.loads(AUDIO_PLAYLIST)
    before = video.dumps()

    m3u8.generate_rendition_reports(
        [("video/index.m3u8", video), ("audio/index.m3u8", audio)]
    )
    after = video.dumps()

    # The stale report is gone; exactly one report points at the other track.
    assert "stale.m3u8" not in after
    assert after.count("#EXT-X-RENDITION-REPORT:") == 1
    assert '#EXT-X-RENDITION-REPORT:URI="../audio/index.m3u8",LAST-MSN=271\n' in after

    def without_reports(content):
        return [
            line
            for line in content.splitlines()
            if not line.startswith("#EXT-X-RENDITION-REPORT:")
        ]

    assert without_reports(before) == without_reports(after)
    # Keys, maps, complete segments and Partial Segments are untouched.
    assert '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"' in after
    assert '#EXT-X-MAP:URI="init.mp4"' in after
    assert '#EXT-X-PART:DURATION=0.33334,URI="filePart272.2.mp4"' in after
    assert len(video.segments) == len(m3u8.loads(VIDEO_PLAYLIST).segments)
    assert [segment.uri for segment in video.segments] == [
        segment.uri for segment in m3u8.loads(VIDEO_PLAYLIST).segments
    ]


def test_report_serialisation_is_unchanged_for_zero_last_part():
    # Regression guard for the pre-existing EXT-X-RENDITION-REPORT output:
    # LAST-PART=0 must not be dropped and parsing must stay the same.
    video = m3u8.loads(AUDIO_PLAYLIST.replace("audioSequence", "videoSequence"))
    video.segments[-1].parts.append(
        m3u8.PartialSegment(None, "videoPart271.0.mp4", 0.33334)
    )
    audio = m3u8.loads(AUDIO_PLAYLIST)

    m3u8.generate_rendition_reports(
        [("video/index.m3u8", video), ("audio/index.m3u8", audio)]
    )

    expected = (
        '#EXT-X-RENDITION-REPORT:URI="../video/index.m3u8",LAST-MSN=271,LAST-PART=0'
    )
    assert expected in audio.dumps()
    reloaded = m3u8.loads(audio.dumps())
    assert reloaded.rendition_reports[0].last_part == 0
    assert reloaded.rendition_reports[0].last_msn == 271


def test_absolute_uris_on_the_same_origin():
    video = m3u8.loads(VIDEO_PLAYLIST, uri="https://cdn.example.com/hls/2M/index.m3u8")
    audio = m3u8.loads(
        AUDIO_PLAYLIST, uri="HTTPS://CDN.EXAMPLE.COM/hls/audio/index.m3u8?token=1"
    )

    m3u8.generate_rendition_reports(
        [
            ("https://cdn.example.com/hls/2M/index.m3u8", video),
            ("HTTPS://CDN.EXAMPLE.COM/hls/audio/index.m3u8?token=1", audio),
        ]
    )

    assert reports_of(video) == [("../audio/index.m3u8?token=1", 271, None)]
    assert reports_of(audio) == [("../2M/index.m3u8", 272, 2)]
    assert (
        video.rendition_reports[0].absolute_uri
        == "https://cdn.example.com/hls/audio/index.m3u8?token=1"
    )


def test_report_base_uri_follows_the_containing_playlist():
    video = m3u8.loads(VIDEO_PLAYLIST, uri="http://example.com/hls/2M/index.m3u8")
    audio = m3u8.loads(AUDIO_PLAYLIST, uri="http://example.com/hls/audio/index.m3u8")

    m3u8.generate_rendition_reports(
        [
            ("http://example.com/hls/2M/index.m3u8", video),
            ("http://example.com/hls/audio/index.m3u8", audio),
        ]
    )

    report = video.rendition_reports[0]
    assert report.base_uri == "http://example.com/hls/2M/"
    assert report.absolute_uri == "http://example.com/hls/audio/index.m3u8"

    # The relative URI is preserved verbatim by dumps/loads.
    reloaded = m3u8.loads(video.dumps(), uri="http://example.com/hls/2M/index.m3u8")
    assert reloaded.rendition_reports[0].uri == "../audio/index.m3u8"
    assert (
        reloaded.rendition_reports[0].absolute_uri
        == "http://example.com/hls/audio/index.m3u8"
    )


def test_tracks_can_be_pairs_track_objects_or_playlist_content():
    video = m3u8.loads(VIDEO_PLAYLIST)
    audio = m3u8.loads(AUDIO_PLAYLIST)

    result = m3u8.generate_rendition_reports(
        [
            ("video/index.m3u8", video),
            RenditionTrack("audio/index.m3u8", audio),
            ("subs/index.m3u8", SUBTITLES_PLAYLIST),
        ]
    )

    assert isinstance(result["subs/index.m3u8"], m3u8.M3U8)
    assert reports_of(video) == [
        ("../audio/index.m3u8", 271, None),
        ("../subs/index.m3u8", 101, None),
    ]
    assert len(result["subs/index.m3u8"].rendition_reports) == 2


def test_single_track_gets_no_reports():
    video = m3u8.loads(VIDEO_PLAYLIST)
    m3u8.generate_rendition_reports({"video/index.m3u8": video})

    assert video.rendition_reports == []
    assert "#EXT-X-RENDITION-REPORT:" not in video.dumps()


@pytest.mark.parametrize(
    "tracks, message",
    [
        (
            {
                "video/index.m3u8": VIDEO_PLAYLIST,
                "video/../video/index.m3u8": AUDIO_PLAYLIST,
            },
            "duplicate",
        ),
        ({"video/index.m3u8": "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"}, "empty"),
        ({"video/index.m3u8": playlists.VARIANT_PLAYLIST}, "master playlist"),
        ({"video/index.m3u8": "not a playlist"}, "empty"),
    ],
)
def test_invalid_tracks_are_rejected(tracks, message):
    with pytest.raises(m3u8.RenditionReportError, match=message):
        m3u8.generate_rendition_reports(tracks)


def test_undeterminable_sequence_number_is_rejected():
    playlist = m3u8.M3U8()
    playlist.segments.append(m3u8.Segment("segment.ts", duration=4))

    with pytest.raises(m3u8.RenditionReportError, match="cannot be determined"):
        m3u8.generate_rendition_reports({"video/index.m3u8": playlist})


def test_illegal_part_layout_is_rejected():
    playlist = m3u8.loads(VIDEO_PLAYLIST)
    playlist.segments[-1].parts[1].uri = None

    with pytest.raises(m3u8.RenditionReportError, match="illegal PART layout"):
        m3u8.generate_rendition_reports({"video/index.m3u8": playlist})

    playlist = m3u8.loads(VIDEO_PLAYLIST)
    missing_uri = m3u8.Segment(None, duration=None)
    missing_uri.parts.append(m3u8.PartialSegment(None, "part.mp4", 0.33334))
    playlist.segments.insert(0, missing_uri)

    with pytest.raises(m3u8.RenditionReportError, match="illegal PART layout"):
        m3u8.generate_rendition_reports({"video/index.m3u8": playlist})


def test_failed_generation_does_not_publish_anything():
    video = m3u8.loads(VIDEO_PLAYLIST)
    audio = m3u8.loads(AUDIO_PLAYLIST)
    before_video = video.dumps()
    before_audio = audio.dumps()

    audio.segments = []
    with pytest.raises(m3u8.RenditionReportError):
        m3u8.generate_rendition_reports(
            {"video/index.m3u8": video, "audio/index.m3u8": audio}
        )

    assert video.dumps() == before_video
    audio.segments = m3u8.loads(AUDIO_PLAYLIST).segments
    assert audio.dumps() == before_audio


def test_cross_origin_tracks_are_rejected():
    video = m3u8.loads(VIDEO_PLAYLIST)
    audio = m3u8.loads(AUDIO_PLAYLIST)

    with pytest.raises(m3u8.RenditionReportError, match="different origins"):
        m3u8.generate_rendition_reports(
            [
                ("https://video.example.com/index.m3u8", video),
                ("https://audio.example.com/index.m3u8", audio),
            ]
        )


def test_existing_rendition_reports_are_still_parsed_as_before():
    playlist = m3u8.loads(playlists.LOW_LATENCY_DELTA_UPDATE_PLAYLIST)

    reports = playlist.rendition_reports
    assert len(reports) == 2
    assert (reports[0].uri, reports[0].last_msn, reports[0].last_part) == (
        "../1M/waitForMSN.php",
        273,
        3,
    )
    assert (reports[1].uri, reports[1].last_msn, reports[1].last_part) == (
        "../4M/waitForMSN.php",
        273,
        3,
    )
    assert (
        '#EXT-X-RENDITION-REPORT:URI="../1M/waitForMSN.php",LAST-MSN=273,LAST-PART=3'
        in playlist.dumps()
    )
