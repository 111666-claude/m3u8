# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.
"""
Apply a key rotation schedule to a Media Playlist.

Event replays and other pre-packaged VOD presentations often encrypt the
media with a sequence of short-lived AES-128 keys instead of a single key
for the whole presentation.  The rotation plan is naturally expressed as a
list of contiguous :class:`KeyInterval` objects: each interval covers a
range of Media Sequence Numbers (or segment indexes) and carries the key
URI and initialization vector to use for the segments inside it.

:func:`apply_key_schedule` turns that plan into the ``Key`` objects of an
existing :class:`~m3u8.model.M3U8` Media Playlist.  Segment URIs,
durations, byte ranges and Media Sequence Numbers are left untouched; only
``EXT-X-KEY`` tags (and therefore the ``keys`` / ``files`` attributes) are
rebuilt, so consecutive segments sharing a key emit a single
``EXT-X-KEY`` tag at the switch point.  An interval with
``METHOD=NONE`` switches the playlist back to clear media.

The inverse operation, :func:`key_intervals_from_playlist`, reads the
intervals back from a playlist (including one parsed from the serialized
output), which makes the schedule round-trip safe.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping

from m3u8.model import Key

METHOD_AES_128 = "AES-128"
METHOD_NONE = "NONE"
SUPPORTED_METHODS = (METHOD_AES_128, METHOD_NONE)

# RFC 8216 section 4.3.2.4: the IV is a 128-bit unsigned integer written
# in hexadecimal notation and prefixed with 0x (or 0X). The prefix is
# optional there, but requiring it keeps the value unambiguous, so the
# schedule never emits a bare number a player could misread.
IV_PATTERN = re.compile(r"0[xX][0-9a-fA-F]{32}\Z")

_SEQUENCE_AXES = frozenset(
    {"media_sequence", "media_sequences", "msn", "sequence", "sequence_number"}
)
_INDEX_AXES = frozenset(
    {"index", "indexes", "segment", "segment_index", "segment_indexes", "segments"}
)


class KeyScheduleError(ValueError):
    """
    Raised when a key rotation schedule cannot be applied.

    This covers intervals that are out of order, overlap or leave a gap,
    intervals outside the playlist, unsupported encryption methods, an
    AES-128 interval without a URI, a malformed IV, and objects that are
    not Media Playlists.
    """


@dataclasses.dataclass(frozen=True)
class KeyInterval:
    """
    A single entry of a key rotation schedule.

    `start`
      inclusive start of the interval, as a Media Sequence Number or as a
      zero-based segment index (see the ``axis`` argument of
      :func:`apply_key_schedule`)

    `end`
      exclusive end of the interval, in the same unit as `start`

    `method`
      ``AES-128`` (the default) or ``NONE`` to switch back to clear media

    `uri`
      key URI; required for ``AES-128`` and forbidden for ``NONE``

    `iv`
      initialization vector, as ``0x`` followed by 32 hexadecimal digits;
      optional for ``AES-128`` (the player then derives it from the Media
      Sequence Number) and forbidden for ``NONE``

    Intervals are half-open: ``KeyInterval(3, 6)`` covers segments 3, 4
    and 5.
    """

    start: int
    end: int
    method: str = METHOD_AES_128
    uri: str | None = None
    iv: str | None = None


def apply_key_schedule(playlist, intervals, axis="auto"):
    """
    Apply a key rotation schedule to a Media Playlist in place.

    `playlist` is the :class:`~m3u8.model.M3U8` object to update and
    `intervals` is an iterable of :class:`KeyInterval` objects.  For
    convenience every item may also be a mapping, or a sequence with the
    same fields as :class:`KeyInterval`.

    `axis` selects the unit used by the intervals:

    * ``"media_sequence"`` -- Media Sequence Numbers (the HLS default);
    * ``"index"`` -- zero-based indexes into ``playlist.segments``;
    * ``"auto"`` (the default) -- Media Sequence Numbers when the first
      interval starts at the first segment's Media Sequence Number and
      segment indexes otherwise.  Because the intervals must cover the
      playlist exactly, this never confuses a valid schedule with one on
      the other axis.

    The intervals must be sorted, contiguous and cover every segment
    exactly once, so the whole playlist has a well defined key.  A clear
    stretch of the playlist (including a leading one) is represented by an
    interval with ``method="NONE"``.  Adjacent intervals with the same
    method, URI and IV describe no actual key switch and are merged, so
    the schedule read back by :func:`key_intervals_from_playlist` is
    canonical.

    The same ``Key`` object is shared by all segments of an interval,
    which is what makes ``dumps()`` emit a single ``EXT-X-KEY`` tag per
    switch point.  Only the key association of the segments is changed:
    their URI, duration, byte range and Media Sequence Number are
    preserved, as are all other playlist tags.  ``playlist.keys`` and
    ``playlist.files`` are rebuilt to match the new schedule.  The
    playlist is returned, so the call can be chained.

    The whole schedule is validated before any segment is touched, so a
    rejected schedule leaves the playlist unchanged.

    :raises KeyScheduleError: for out of order, overlapping or
        non-contiguous intervals, intervals outside the playlist,
        unsupported methods, missing key URIs, malformed IVs, or when
        `playlist` is not a Media Playlist.
    """
    segments = _media_playlist_segments(playlist)
    schedule = [
        _coerce_interval(item, position) for position, item in enumerate(intervals)
    ]
    if not schedule:
        raise KeyScheduleError("a key schedule needs at least one interval")
    _validate_order(schedule)

    normalized = [
        _normalize_interval(item, position) for position, item in enumerate(schedule)
    ]
    media_sequences = _start_numbers("media_sequence", segments, playlist)
    axis = _resolve_axis(axis, schedule[0], media_sequences)
    if axis == "media_sequence":
        _validate_media_sequences(media_sequences)
        offset = media_sequences[0]
    else:
        offset = 0
    bounds = _validate_coverage(schedule, offset, len(segments), axis)

    entries = [
        (start, end, method, uri, iv)
        for (start, end), (method, uri, iv) in zip(bounds, normalized)
    ]
    for position in range(len(entries) - 1, 0, -1):
        start, end, method, uri, iv = entries[position]
        previous = entries[position - 1]
        if (method, uri, iv) == previous[2:]:  # same key, no real switch
            entries[position - 1] = (previous[0], end, method, uri, iv)
            del entries[position]

    for start, end, method, uri, iv in entries:
        if method == METHOD_NONE:
            # A playlist that starts clear needs no EXT-X-KEY tag at all;
            # after an encrypted interval an explicit METHOD=NONE is
            # required to switch the following segments back to clear.
            key = None if start == 0 else _build_key(playlist, METHOD_NONE)
        else:
            key = _build_key(playlist, method, uri, iv)
        for segment in segments[start:end]:
            segment.key = key

    _rebuild_keys_and_files(playlist)
    return playlist


def key_intervals_from_playlist(playlist, axis="media_sequence"):
    """
    Read the key rotation schedule currently applied to a playlist.

    Consecutive segments sharing the same method, URI and IV are merged
    into a single :class:`KeyInterval`, so applying the returned list with
    :func:`apply_key_schedule` reproduces the playlist's key layout.
    Clear segments (no ``EXT-X-KEY``, or a ``METHOD=NONE`` switch) are
    reported as intervals with ``method="NONE"``.

    `axis` may be ``"media_sequence"`` (the default, using each segment's
    Media Sequence Number) or ``"index"`` (zero-based segment indexes).
    ``"auto"`` is accepted and behaves like ``"media_sequence"``.

    Only ``AES-128`` and ``NONE`` intervals can be applied back with
    :func:`apply_key_schedule`; other methods (such as ``SAMPLE-AES``
    found in an existing playlist) are reported as they are.
    """
    segments = _media_playlist_segments(playlist)
    axis = _normalize_axis(axis)
    if axis == "auto":
        axis = "media_sequence"
    start_numbers = _start_numbers(axis, segments, playlist)

    intervals = []
    for segment, start in zip(segments, start_numbers):
        method, uri, iv = _key_signature(segment.key)
        if intervals and (
            intervals[-1].method,
            intervals[-1].uri,
            intervals[-1].iv,
        ) == (method, uri, iv):
            intervals[-1] = dataclasses.replace(intervals[-1], end=start + 1)
        else:
            intervals.append(KeyInterval(start, start + 1, method, uri, iv))
    return intervals


def _media_playlist_segments(playlist):
    if getattr(playlist, "is_variant", False):
        raise KeyScheduleError(
            "a key rotation schedule requires a Media Playlist, not a Master Playlist"
        )
    segments = getattr(playlist, "segments", None)
    if not segments:
        raise KeyScheduleError(
            "a key rotation schedule requires a playlist with segments"
        )
    return segments


def _coerce_interval(item, position):
    if isinstance(item, KeyInterval):
        return item
    try:
        if isinstance(item, Mapping):
            return KeyInterval(**item)
        return KeyInterval(*item)
    except (TypeError, ValueError) as error:
        raise KeyScheduleError(
            f"key interval #{position} must be a KeyInterval, a mapping or a "
            f"(start, end, method, uri, iv) sequence: {error}"
        ) from None


def _normalize_interval(interval, position):
    if not _is_integer(interval.start) or not _is_integer(interval.end):
        raise KeyScheduleError(
            f"key interval #{position} has non-integer bounds: "
            f"{interval.start!r}..{interval.end!r}"
        )
    if interval.start < 0:
        raise KeyScheduleError(
            f"key interval #{position} has a negative start: {interval.start}"
        )
    if interval.end <= interval.start:
        raise KeyScheduleError(
            f"key interval #{position} is empty or reversed: "
            f"[{interval.start}, {interval.end})"
        )

    method = interval.method
    if not isinstance(method, str):
        raise KeyScheduleError(
            f"key interval #{position} has an invalid method: {method!r}"
        )
    method = method.strip().upper()
    if method not in SUPPORTED_METHODS:
        supported = " and ".join(SUPPORTED_METHODS)
        raise KeyScheduleError(
            f"key interval #{position} uses an unsupported method "
            f"{interval.method!r}; only {supported} are supported"
        )

    uri = interval.uri
    iv = interval.iv
    if method == METHOD_NONE:
        if uri not in (None, ""):
            raise KeyScheduleError(
                f"key interval #{position} uses METHOD=NONE and must not "
                "carry a key URI"
            )
        if iv not in (None, ""):
            raise KeyScheduleError(
                f"key interval #{position} uses METHOD=NONE and must not carry an IV"
            )
        return method, None, None

    if not isinstance(uri, str) or not uri.strip():
        raise KeyScheduleError(
            f"key interval #{position} uses AES-128 and requires a non-empty key URI"
        )
    if iv is not None and not (isinstance(iv, str) and IV_PATTERN.fullmatch(iv)):
        raise KeyScheduleError(
            f"key interval #{position} has an invalid IV {iv!r}; expected "
            "0x followed by 32 hexadecimal digits"
        )
    return method, uri, iv


def _validate_order(schedule):
    for position in range(1, len(schedule)):
        previous = schedule[position - 1]
        if not (_is_integer(previous.start) and _is_integer(schedule[position].start)):
            # non-integer bounds are reported by _normalize_interval
            continue
        if schedule[position].start < previous.start:
            raise KeyScheduleError(
                f"key intervals are out of order: interval #{position} "
                f"starts at {schedule[position].start} after an interval "
                f"starting at {schedule[position - 1].start}"
            )


def _validate_media_sequences(media_sequences):
    for position, number in enumerate(media_sequences):
        if number != media_sequences[0] + position:
            raise KeyScheduleError(
                "the segments of the playlist have non-contiguous Media "
                f"Sequence Numbers ({media_sequences[0]}..{media_sequences[-1]} "
                f"with a jump at index {position}); use segment indexes instead"
            )


def _validate_coverage(schedule, offset, segment_count, axis):
    unit = "Media Sequence Number" if axis == "media_sequence" else "segment index"
    first = offset
    last = offset + segment_count - 1
    bounds = []
    expected = offset
    for position, interval in enumerate(schedule):
        if interval.start < first or interval.end > last + 1:
            raise KeyScheduleError(
                f"key interval #{position} [{interval.start}, "
                f"{interval.end}) is out of bounds: the playlist covers "
                f"{unit} {first}..{last}"
            )
        if interval.start < expected:
            raise KeyScheduleError(
                f"key intervals overlap: interval #{position} starts at "
                f"{unit} {interval.start} but the previous interval ends at "
                f"{unit} {expected}"
            )
        if interval.start > expected:
            raise KeyScheduleError(
                f"key schedule has a gap at {unit} {expected}: interval "
                f"#{position} starts at {interval.start}"
            )
        bounds.append((interval.start - offset, interval.end - offset))
        expected = interval.end

    if expected != last + 1:
        raise KeyScheduleError(
            "key schedule has a gap at the end of the playlist: it covers "
            f"up to {unit} {expected - 1} but the last segment is "
            f"{unit} {last}"
        )
    return bounds


def _resolve_axis(axis, first_interval, media_sequences):
    axis = _normalize_axis(axis)
    if axis != "auto":
        return axis
    if first_interval.start == media_sequences[0]:
        return "media_sequence"
    if 0 <= first_interval.start < len(media_sequences):
        return "index"
    return "media_sequence"


def _start_numbers(axis, segments, playlist):
    if axis == "index":
        return list(range(len(segments)))

    first = getattr(segments[0], "media_sequence", None)
    if not _is_integer(first):
        first = getattr(playlist, "media_sequence", 0)
        if not _is_integer(first):
            first = 0

    numbers = []
    for position, segment in enumerate(segments):
        number = getattr(segment, "media_sequence", None)
        numbers.append(number if _is_integer(number) else first + position)
    return numbers


def _normalize_axis(axis):
    if not isinstance(axis, str):
        raise KeyScheduleError(f"invalid key interval axis: {axis!r}")
    normalized = axis.strip().lower().replace("-", "_")
    if normalized == "auto":
        return "auto"
    if normalized in _SEQUENCE_AXES:
        return "media_sequence"
    if normalized in _INDEX_AXES:
        return "index"
    raise KeyScheduleError(
        f"unknown key interval axis {axis!r}; expected 'auto', "
        "'media_sequence' or 'index'"
    )


def _key_signature(key):
    if key is None:
        return METHOD_NONE, None, None
    method = key.method.strip().upper() if isinstance(key.method, str) else key.method
    if method == METHOD_NONE:
        # EXT-X-KEY:METHOD=NONE,URI="" means exactly the same as a bare
        # METHOD=NONE, so both spellings map to the same interval.
        return METHOD_NONE, None, None
    return method, key.uri, key.iv


def _build_key(playlist, method, uri=None, iv=None):
    key = Key(method, playlist.base_uri, uri=uri or None, iv=iv or None)
    if method != METHOD_NONE and playlist.base_path is not None:
        # Keep the same behaviour as keys created while parsing the
        # playlist: a configured base_path rewrites the key URI.
        key.base_path = playlist.base_path
    return key


def _rebuild_keys_and_files(playlist):
    keys = []
    for segment in playlist.segments:
        if segment.key not in keys:
            keys.append(segment.key)
    playlist.keys = keys

    files = []
    for key in keys:
        if key and key.uri and key.uri not in files:
            files.append(key.uri)
    files.extend(playlist.segments.uri)
    playlist.files = files


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)
