# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

"""
Tests for EXT-X-SESSION-KEY resolution against per-segment EXT-X-KEY tags.

Covers association by METHOD/KEYFORMAT/KEYFORMATVERSIONS, key rotation and
METHOD=NONE clear stretches, absolute URI handling and every error path
(missing session key, conflicting definitions, bad URI, missing KEYFORMAT
defaulting, and cross-master misuse).
"""

import copy

import playlists
import pytest

import m3u8
from m3u8.model import (
    ConflictingSessionKeyError,
    SessionKey,
    SessionKeyNotFoundError,
    SessionKeyURIError,
    UnrelatedPlaylistError,
    key_identity,
    resolve_session_keys,
)

MASTER_BASE_URI = playlists.MASTER_PLAYLIST_WITH_SESSION_KEYS_BASE_URI
MEDIA_BASE_URI = playlists.MEDIA_PLAYLIST_WITH_ROTATION_AND_CLEAR_BASE_URI


def make_master():
    return m3u8.M3U8(
        playlists.MASTER_PLAYLIST_WITH_SESSION_KEYS, base_uri=MASTER_BASE_URI
    )


def make_media():
    return m3u8.M3U8(
        playlists.MEDIA_PLAYLIST_WITH_ROTATION_AND_CLEAR, base_uri=MEDIA_BASE_URI
    )


def test_resolves_session_keys_for_each_segment():
    master = make_master()
    media = make_media()

    resolved = media.resolve_session_keys(master, media_uri="media.m3u8")

    assert len(resolved) == len(media.segments)

    # seg1/seg2 use the first AES-128 session key
    aes1 = master.session_keys[0]
    assert resolved[0] is aes1
    assert resolved[1] is aes1
    assert isinstance(resolved[0], SessionKey)
    assert resolved[0].absolute_uri == "https://priv.example.com/hls/keys/aes-1.bin"

    # seg3 is explicitly clear (METHOD=NONE)
    assert resolved[2] is None
    assert media.segments[2].key.method == "NONE"

    # seg4 uses the SAMPLE-AES / FairPlay session key (second key format)
    sample_aes = master.session_keys[1]
    assert resolved[3] is sample_aes
    assert resolved[3].absolute_uri == "skd://fairplay-1"
    assert resolved[3].keyformat == "com.apple.streamingkeydelivery"

    # seg5 rotated onto the Widevine session key (third key format)
    widevine = master.session_keys[2]
    assert resolved[4] is widevine
    assert resolved[4].absolute_uri == "skd://widevine-1"
    assert resolved[4].keyformat == "com.widevine.key"
    assert resolved[4].keyformatversions == "2"

    # seg6 rotates back onto the AES-128 session key
    assert resolved[5] is aes1
    assert resolved[5].absolute_uri == "https://priv.example.com/hls/keys/aes-1.bin"


def test_module_level_function_matches_method():
    master = make_master()
    media = make_media()

    assert resolve_session_keys(media, master, media_uri="media.m3u8") == (
        media.resolve_session_keys(master, media_uri="media.m3u8")
    )


def test_key_rotation_follows_media_ext_x_key_order():
    master = make_master()
    media = make_media()

    resolved = media.resolve_session_keys(master, media_uri="media.m3u8")
    methods = [None if key is None else key.method for key in resolved]

    assert methods == [
        "AES-128",
        "AES-128",
        None,  # METHOD=NONE clear stretch
        "SAMPLE-AES",  # FairPlay
        "SAMPLE-AES",  # rotated to Widevine
        "AES-128",  # rotated back
    ]


def test_clear_segment_without_any_ext_x_key_resolves_none():
    master = make_master()
    media = m3u8.M3U8(playlists.MEDIA_PLAYLIST_STARTING_CLEAR, base_uri=MEDIA_BASE_URI)

    resolved = media.resolve_session_keys(master, media_uri="media.m3u8")

    assert resolved[0] is None
    assert resolved[1] is master.session_keys[0]


def test_method_none_takes_precedence_over_session_key():
    master = make_master()
    media = make_media()

    resolved = media.resolve_session_keys(master, media_uri="media.m3u8")

    # Even though the master preloads AES keys, the explicit METHOD=NONE
    # switch in the media playlist wins for seg3.
    assert resolved[2] is None


def test_absolute_uris_are_resolved_against_master_base_uri():
    master = make_master()
    media = make_media()

    resolved = media.resolve_session_keys(master, media_uri="media.m3u8")

    assert resolved[0].absolute_uri == "https://priv.example.com/hls/keys/aes-1.bin"
    # Original objects keep their relative URI untouched.
    assert resolved[0].uri == "keys/aes-1.bin"


def test_missing_keyformat_and_versions_default_to_identity_and_1():
    master = make_master()
    identity = key_identity(master.session_keys[0])

    assert identity == ("AES-128", "identity", "1")

    # A media key without KEYFORMAT still matches the implicit identity key.
    media = m3u8.M3U8(playlists.MEDIA_PLAYLIST_WITH_KEY_IV, base_uri=MEDIA_BASE_URI)
    resolved = media.resolve_session_keys(master, media_uri="media.m3u8")

    assert resolved[0] is master.session_keys[0]


def test_media_key_iv_does_not_conflict_with_session_key():
    master = make_master()
    media = m3u8.M3U8(playlists.MEDIA_PLAYLIST_WITH_KEY_IV, base_uri=MEDIA_BASE_URI)

    # RFC 8216 4.3.4.5: EXT-X-SESSION-KEY carries no IV; the media EXT-X-KEY
    # IV must therefore not be treated as a conflict.
    resolved = media.resolve_session_keys(master, media_uri="media.m3u8")

    assert len(resolved) == 1
    assert resolved[0].uri == "keys/aes-1.bin"


def test_unknown_key_identity_raises_not_found():
    master = make_master()
    media = m3u8.M3U8(
        playlists.MEDIA_PLAYLIST_WITH_UNKNOWN_SESSION_KEY, base_uri=MEDIA_BASE_URI
    )

    with pytest.raises(SessionKeyNotFoundError) as excinfo:
        media.resolve_session_keys(master, media_uri="media.m3u8")

    error = excinfo.value
    assert error.identity == (
        "SAMPLE-AES",
        "com.widevine.key",
        "1",
    )
    assert error.key.uri == "skd://widevine-1"


def test_conflicting_session_key_definitions_raise():
    master = m3u8.M3U8(
        playlists.MASTER_PLAYLIST_WITH_CONFLICTING_SESSION_KEYS,
        base_uri=MASTER_BASE_URI,
    )
    media = make_media()

    with pytest.raises(ConflictingSessionKeyError):
        media.resolve_session_keys(master, media_uri="media.m3u8")


def test_conflicting_key_uri_between_media_and_session_raises():
    master = make_master()
    media = m3u8.M3U8(
        playlists.MEDIA_PLAYLIST_WITH_CONFLICTING_KEY_URI, base_uri=MEDIA_BASE_URI
    )

    with pytest.raises(ConflictingSessionKeyError):
        media.resolve_session_keys(master, media_uri="media.m3u8")


def test_identical_duplicate_session_keys_are_accepted():
    master = m3u8.M3U8(
        playlists.MASTER_PLAYLIST_WITH_SESSION_KEYS.replace(
            '#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="skd://widevine-1",'
            'KEYFORMAT="com.widevine.key",KEYFORMATVERSIONS="2"\n',
            '#EXT-X-SESSION-KEY:METHOD=AES-128,URI="keys/aes-1.bin"\n',
        ),
        base_uri=MASTER_BASE_URI,
    )
    media = m3u8.M3U8(playlists.MEDIA_PLAYLIST_WITH_KEY_IV, base_uri=MEDIA_BASE_URI)

    # The second AES-128/identity entry repeats the first exactly, so the
    # duplicate is tolerated instead of raising a conflict.
    assert len(master.session_keys) == 3
    resolved = media.resolve_session_keys(master, media_uri="media.m3u8")

    assert resolved[0] is master.session_keys[0]


def test_bad_relative_uri_without_base_uri_raises():
    master = m3u8.M3U8(playlists.MASTER_PLAYLIST_WITH_RELATIVE_SESSION_KEY)
    media = m3u8.M3U8(playlists.MEDIA_PLAYLIST_WITH_RELATIVE_KEY)

    with pytest.raises(SessionKeyURIError):
        media.resolve_session_keys(master, media_uri="media.m3u8")


def test_session_key_in_media_playlist_is_cross_master_misuse():
    fake_master = m3u8.M3U8(playlists.MEDIA_PLAYLIST_WITH_ILLEGAL_SESSION_KEY)
    media = m3u8.M3U8(playlists.MEDIA_PLAYLIST_WITH_RELATIVE_KEY)

    assert fake_master.is_variant is False
    with pytest.raises(UnrelatedPlaylistError):
        media.resolve_session_keys(fake_master)


def test_media_not_referenced_by_master_raises():
    master = make_master()
    media = make_media()

    with pytest.raises(UnrelatedPlaylistError):
        media.resolve_session_keys(master, media_uri="some-other/media.m3u8")


def test_other_master_referencing_different_media_raises():
    master = m3u8.M3U8(playlists.OTHER_MASTER_PLAYLIST, base_uri=MASTER_BASE_URI)
    media = make_media()

    with pytest.raises(UnrelatedPlaylistError):
        media.resolve_session_keys(master, media_uri="media.m3u8")


def test_media_uri_check_accepts_absolute_form():
    master = make_master()
    media = make_media()

    resolved = media.resolve_session_keys(
        master, media_uri="https://priv.example.com/hls/media.m3u8"
    )

    assert len(resolved) == len(media.segments)


def test_media_uri_check_skipped_when_not_provided():
    master = make_master()
    media = make_media()

    # No media_uri: callers that already know the relationship skip the
    # membership check; resolution itself still works.
    resolved = media.resolve_session_keys(master)

    assert len(resolved) == len(media.segments)


def test_non_m3u8_arguments_raise_type_error():
    master = make_master()

    with pytest.raises(TypeError):
        resolve_session_keys("not a playlist", master)
    with pytest.raises(TypeError):
        resolve_session_keys(master, "not a playlist")


def test_resolution_does_not_mutate_playlists_or_change_dumps():
    master = make_master()
    media = make_media()
    master_before = copy.deepcopy(master)
    media_before = copy.deepcopy(media)

    media.resolve_session_keys(master, media_uri="media.m3u8")

    assert master.dumps() == master_before.dumps()
    assert media.dumps() == media_before.dumps()
    assert [key.uri for key in master.session_keys] == [
        key.uri for key in master_before.session_keys
    ]
    for segment, before in zip(media.segments, media_before.segments):
        assert (segment.key is None and before.key is None) or (
            segment.key.uri == before.key.uri
            and segment.key.method == before.key.method
        )


def test_resolved_entries_are_the_original_session_key_objects():
    master = make_master()
    media = make_media()

    resolved = media.resolve_session_keys(master, media_uri="media.m3u8")

    for key in resolved:
        if key is not None:
            assert key in master.session_keys
