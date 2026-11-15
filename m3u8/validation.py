# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.
"""
Offline semantic validation for HLS Media Playlists.

Parsing a playlist only tells whether its syntax is understood.  A playlist
that parses can still be rejected by players because of conflicts between
tags: an ``EXTINF`` longer than ``EXT-X-TARGETDURATION``, a byte range that
depends on a preceding segment that is not a sub-range of the same resource,
an fMP4 segment without an applicable ``EXT-X-MAP``, or a VOD playlist
without ``EXT-X-ENDLIST``.

:func:`validate_playlist` reports those problems without touching the
playlist: the content, URIs and Media Sequence Numbers are only read, never
normalized or rewritten.  Diagnostics are returned in playlist order, each
one carrying a stable rule identifier, a severity, and a locatable tag or
segment position.

Only Media Playlists are validated.  Master (multivariant) playlists and
playlists with no Media Segments do not use the rules below and therefore
produce no diagnostics.  Rules whose input fields are absent (for example
``EXT-X-TARGETDURATION``) are skipped rather than guessed at.
"""

from __future__ import annotations

import decimal
import os
import re
from dataclasses import asdict, dataclass

from m3u8 import protocol
from m3u8.model import M3U8
from m3u8.parser import parse

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

RULE_EXTINF_EXCEEDS_TARGET_DURATION = "EXTINF_DURATION_EXCEEDS_TARGET_DURATION"
RULE_BYTERANGE_OFFSET_REQUIRED = "BYTERANGE_OFFSET_REQUIRED"
RULE_BYTERANGE_INVALID = "BYTERANGE_INVALID"
RULE_FMP4_MAP_MISSING = "FMP4_MAP_MISSING"
RULE_MAP_URI_MISSING = "MAP_URI_MISSING"
RULE_MAP_BYTERANGE_INVALID = "MAP_BYTERANGE_INVALID"
RULE_VOD_MISSING_ENDLIST = "VOD_MISSING_ENDLIST"

#: Metadata for every rule emitted by :func:`validate_playlist`.
RULES = {
    RULE_EXTINF_EXCEEDS_TARGET_DURATION: {
        "severity": SEVERITY_ERROR,
        "description": (
            "An EXTINF duration, rounded to the nearest integer, is greater "
            "than EXT-X-TARGETDURATION."
        ),
        "reference": "RFC 8216 section 4.3.3.1",
    },
    RULE_BYTERANGE_OFFSET_REQUIRED: {
        "severity": SEVERITY_ERROR,
        "description": (
            "An EXT-X-BYTERANGE without an offset does not follow a byte "
            "range of the same media resource."
        ),
        "reference": "RFC 8216 section 4.3.2.2",
    },
    RULE_BYTERANGE_INVALID: {
        "severity": SEVERITY_ERROR,
        "description": "An EXT-X-BYTERANGE value is not <length>[@<offset>].",
        "reference": "RFC 8216 section 4.3.2.2",
    },
    RULE_FMP4_MAP_MISSING: {
        "severity": SEVERITY_ERROR,
        "description": (
            "A fragmented MP4 Media Segment has no applicable EXT-X-MAP. "
            "The severity is ``warning`` when the container was inferred "
            "from an ambiguous extension such as ``.mp4``."
        ),
        "reference": "RFC 8216 section 4.3.2.5",
    },
    RULE_MAP_URI_MISSING: {
        "severity": SEVERITY_ERROR,
        "description": "An EXT-X-MAP tag is missing its required URI attribute.",
        "reference": "RFC 8216 section 4.3.2.5",
    },
    RULE_MAP_BYTERANGE_INVALID: {
        "severity": SEVERITY_ERROR,
        "description": "An EXT-X-MAP BYTERANGE is not <length>[@<offset>].",
        "reference": "RFC 8216 section 4.3.2.5",
    },
    RULE_VOD_MISSING_ENDLIST: {
        "severity": SEVERITY_ERROR,
        "description": (
            "The playlist is declared VOD but has no EXT-X-ENDLIST, so it "
            "claims more segments can still be added."
        ),
        "reference": "RFC 8216 sections 4.3.3.4, 4.3.3.5 and 6.2.1",
    },
}


@dataclass(frozen=True)
class Diagnostic:
    """
    A single semantic problem found in a Media Playlist.

    ``rule`` is a stable identifier (see :data:`RULES`), ``severity`` is
    either ``"error"`` or ``"warning"``.  ``line`` is the 1-based line of
    the tag or segment URI that locates the problem (``None`` when the
    playlist object no longer has its source text).  ``tag`` is the tag on
    that line, when the problem is attached to a tag.  ``segment_index`` is
    the 0-based index of the affected Media Segment and ``media_sequence``
    is the segment's Media Sequence Number, both left untouched by
    validation.  ``uri`` is the segment URI as written in the playlist.
    """

    rule: str
    severity: str
    message: str
    line: int | None = None
    tag: str | None = None
    segment_index: int | None = None
    media_sequence: int | None = None
    uri: str | None = None

    @property
    def rule_id(self):
        """Alias for ``rule``, for callers that prefer an explicit name."""
        return self.rule

    def as_dict(self):
        """Return the diagnostic as a plain, JSON-friendly dictionary."""
        return asdict(self)

    def __str__(self):
        where = "playlist"
        if self.line is not None:
            where = f"line {self.line}"
        elif self.segment_index is not None:
            where = f"segment {self.segment_index}"
        return f"[{self.severity}] {self.rule} at {where}: {self.message}"


@dataclass
class _SegmentPosition:
    """Source locations for one parsed Media Segment."""

    extinf_line: int | None = None
    byterange_line: int | None = None
    uri_line: int | None = None
    uri: str | None = None


_BYTERANGE_RE = re.compile(r"^(\d+)(?:@(\d+))?$")

#: File extensions that unambiguously identify fragmented MP4 Media
#: Segments.  HLS has no explicit container attribute, so the segment URI is
#: the only offline signal; MPEG-2 TS, packed audio and image segments
#: (.ts, .aac, .mp3, .jpg, ...) are deliberately absent, so the rule does not
#: apply to them.
_FMP4_EXTENSIONS = frozenset({".m4s", ".fmp4", ".cmfv", ".cmfa"})

#: Extensions shared by fragmented and progressive/packed-audio media
#: (.mp4, .m4a, .m4v, ...).  A missing EXT-X-MAP there is only reported when
#: the playlist itself shows fMP4 intent: it already uses EXT-X-MAP
#: somewhere, or it declares the protocol version (6 or greater) that
#: defines EXT-X-MAP for Media Playlists.  Even then it is a warning, since
#: the container cannot be established from the URI alone.
_AMBIGUOUS_FMP4_EXTENSIONS = frozenset({".mp4", ".m4a", ".m4v", ".m4p", ".m4f"})
_EXT_X_MAP_VERSION = 6

#: Playlist types whose segments must all be present, so the absence of
#: EXT-X-ENDLIST is a defect rather than a normal live window.
_FINAL_PLAYLIST_TYPES = frozenset({"vod"})


def validate_playlist(playlist):
    """
    Validate an HLS Media Playlist and return a list of :class:`Diagnostic`.

    ``playlist`` may be a string with the playlist content or an
    :class:`m3u8.M3U8` object.  Validation is read-only: the playlist,
    its URIs and its Media Sequence Numbers are never modified.

    Diagnostics are ordered by their position in the playlist.  A valid
    live or VOD Media Playlist produces an empty list, as do Master
    playlists and Media Playlists without segments (the rules are not
    applicable there).  Parse errors are not semantic errors and are
    propagated unchanged.
    """
    data, content = _playlist_data(playlist)

    if data.get("is_variant"):
        # The media-playlist rules below do not apply to Master playlists.
        return []

    segments = data.get("segments") or []
    if not _has_media_segments(segments):
        # An empty Media Playlist has no segment or tag conflict to report.
        # It is reported as clean rather than guessing at missing fields.
        return []

    positions, map_lines, playlist_type_line = _source_positions(data, content)

    diagnostics = []
    diagnostics.extend(_check_target_durations(data, segments, positions))
    diagnostics.extend(_check_byteranges(data, segments, positions))
    diagnostics.extend(_check_maps(data, map_lines))
    diagnostics.extend(_check_fmp4_maps(data, segments, positions))
    diagnostics.extend(_check_vod_endlist(data, playlist_type_line))

    diagnostics.sort(key=_diagnostic_sort_key)
    return diagnostics


# ``validate`` is a short alias for callers that prefer the module name to
# provide the context.  Both return the same diagnostics.
validate = validate_playlist


def _has_media_segments(segments):
    return any(segment.get("uri") is not None for segment in segments)


def _source_positions(data, content):
    """
    Return ``(positions, map_lines, playlist_type_line)`` for the source.

    Line numbers are only trusted when the rescan lines up with the parsed
    data (same number of records, same URIs).  A playlist modified after
    parsing, or one built without source text, simply loses its line
    numbers; the segment indexes and Media Sequence Numbers remain valid.
    """
    positions, map_lines, playlist_type_line = _scan_source(content)
    segments = data.get("segments") or []
    if len(positions) != len(segments):
        return [], [], playlist_type_line
    for position, segment in zip(positions, segments):
        if position.uri != segment.get("uri"):
            return [], [], playlist_type_line
    maps = data.get("segment_map") or []
    if len(map_lines) != len(maps):
        map_lines = []
    return positions, map_lines, playlist_type_line


def _playlist_data(playlist):
    """Return ``(parse_data, source_content)`` for the supported inputs."""
    if isinstance(playlist, M3U8):
        content = getattr(playlist, "_source_content", None)
        return playlist.data or {}, content
    if isinstance(playlist, bytes):
        content = playlist.decode("utf-8")
    elif isinstance(playlist, str):
        content = playlist
    else:
        raise TypeError(
            "validate_playlist() expects a str, bytes or m3u8.M3U8 object, "
            f"got {type(playlist).__name__}"
        )
    return parse(content), content


def _scan_source(content):
    """
    Locate tags and segments in the source text.

    The parser does not keep line numbers, so validation does a second,
    read-only pass over the same lines it parsed.  The segment bookkeeping
    mirrors the parser's ``expect_segment`` state so that every record
    lines up with ``data["segments"]``.
    """
    if not isinstance(content, str):
        return [], [], None

    positions = []
    map_lines = []
    playlist_type_line = None
    current = None
    expect_segment = False

    for lineno, raw_line in enumerate(content.strip().splitlines(), 1):
        line = raw_line.strip()

        if line.startswith(protocol.ext_x_map):
            map_lines.append(lineno)
            continue

        if line.startswith(protocol.ext_x_playlist_type):
            # The parser keeps the last value, so point at the last tag.
            playlist_type_line = lineno
            continue

        if line.startswith(protocol.extinf):
            if current is None:
                current = _SegmentPosition()
            current.extinf_line = lineno
            expect_segment = True
            continue

        if line.startswith(protocol.ext_x_byterange):
            if current is None:
                current = _SegmentPosition()
            current.byterange_line = lineno
            expect_segment = True
            continue

        if line.startswith(protocol.ext_x_bitrate) or _is_part_tag(line):
            if current is None:
                current = _SegmentPosition()
            continue

        if line and not line.startswith("#") and expect_segment:
            current.uri = line
            current.uri_line = lineno
            positions.append(current)
            current = None
            expect_segment = False

    # The parser keeps a trailing segment that has tags but no URI.
    if current is not None:
        positions.append(current)

    return positions, map_lines, playlist_type_line


def _is_part_tag(line):
    # ``EXT-X-PART-INF`` must not be mistaken for ``EXT-X-PART``.
    return line.startswith(protocol.ext_x_part + ":")


def _position(positions, index):
    if index < len(positions):
        return positions[index]
    return None


def _segment_sequence(data, index):
    sequence = data.get("media_sequence")
    if isinstance(sequence, bool):
        return None
    try:
        sequence = int(sequence)
    except (TypeError, ValueError):
        return None
    return sequence + index


def _round_half_up(value):
    try:
        number = decimal.Decimal(str(value))
    except (decimal.InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return int(number.quantize(decimal.Decimal(1), rounding=decimal.ROUND_HALF_UP))


def _check_target_durations(data, segments, positions):
    target_duration = data.get("targetduration")
    if isinstance(target_duration, bool) or not isinstance(target_duration, int):
        # Missing or non-integer target duration: the rule does not apply.
        return []
    if data.get("is_images_only") or data.get("tiles"):
        # Image Media Playlists count each tile inside an EXT-X-TILES group
        # against the target duration, while EXTINF spans the whole group,
        # so the Media Segment comparison does not apply.
        return []

    diagnostics = []
    for index, segment in enumerate(segments):
        duration = segment.get("duration")
        if duration is None:
            continue
        rounded = _round_half_up(duration)
        if rounded is None or rounded <= target_duration:
            continue

        position = _position(positions, index)
        line, tag = _segment_location(position, prefer="extinf")
        diagnostics.append(
            Diagnostic(
                rule=RULE_EXTINF_EXCEEDS_TARGET_DURATION,
                severity=SEVERITY_ERROR,
                message=(
                    f"EXTINF duration {duration!r}s rounds to {rounded}s, "
                    f"which exceeds EXT-X-TARGETDURATION {target_duration}s"
                ),
                line=line,
                tag=tag,
                segment_index=index,
                media_sequence=_segment_sequence(data, index),
                uri=segment.get("uri"),
            )
        )
    return diagnostics


def _check_byteranges(data, segments, positions):
    diagnostics = []
    previous = None

    for index, segment in enumerate(segments):
        uri = segment.get("uri")
        if uri is None:
            # A tag-only record is not a Media Segment: the dependency rule
            # does not apply to it.
            previous = segment
            continue

        byterange = segment.get("byterange")
        if byterange is None:
            previous = segment
            continue

        position = _position(positions, index)
        line = position.byterange_line if position else None

        match = _BYTERANGE_RE.match(str(byterange).strip())
        if match is None:
            diagnostics.append(
                Diagnostic(
                    rule=RULE_BYTERANGE_INVALID,
                    severity=SEVERITY_ERROR,
                    message=(
                        f"EXT-X-BYTERANGE value {byterange!r} is not "
                        "'<length>' or '<length>@<offset>'"
                    ),
                    line=line,
                    tag=protocol.ext_x_byterange,
                    segment_index=index,
                    media_sequence=_segment_sequence(data, index),
                    uri=uri,
                )
            )
            previous = segment
            continue

        if match.group(2) is None:
            reason = _missing_offset_reason(previous, segment)
            if reason is not None:
                diagnostics.append(
                    Diagnostic(
                        rule=RULE_BYTERANGE_OFFSET_REQUIRED,
                        severity=SEVERITY_ERROR,
                        message=(
                            "EXT-X-BYTERANGE without an offset must follow a "
                            f"byte range of the same media resource: {reason}"
                        ),
                        line=line,
                        tag=protocol.ext_x_byterange,
                        segment_index=index,
                        media_sequence=_segment_sequence(data, index),
                        uri=uri,
                    )
                )

        previous = segment

    return diagnostics


def _missing_offset_reason(previous, segment):
    """Return why a BYTERANGE without an offset has no valid predecessor."""
    if previous is None:
        return "there is no previous Media Segment"
    if not previous.get("uri"):
        return "the previous Media Segment has no media URI"
    if previous.get("uri") != segment.get("uri"):
        return (
            f"the previous Media Segment uses resource {previous.get('uri')!r}, "
            f"not {segment.get('uri')!r}"
        )
    previous_byterange = previous.get("byterange")
    if previous_byterange is None:
        return "the previous Media Segment is not a byte sub-range"
    if _BYTERANGE_RE.match(str(previous_byterange).strip()) is None:
        return "the previous Media Segment has an invalid byte range"
    return None


def _check_maps(data, map_lines):
    diagnostics = []
    for index, segment_map in enumerate(data.get("segment_map") or []):
        line = map_lines[index] if index < len(map_lines) else None
        if not segment_map.get("uri"):
            diagnostics.append(
                Diagnostic(
                    rule=RULE_MAP_URI_MISSING,
                    severity=SEVERITY_ERROR,
                    message="EXT-X-MAP is missing the required URI attribute",
                    line=line,
                    tag=protocol.ext_x_map,
                )
            )
        byterange = segment_map.get("byterange")
        if (
            byterange is not None
            and _BYTERANGE_RE.match(str(byterange).strip()) is None
        ):
            diagnostics.append(
                Diagnostic(
                    rule=RULE_MAP_BYTERANGE_INVALID,
                    severity=SEVERITY_ERROR,
                    message=(
                        f"EXT-X-MAP BYTERANGE value {byterange!r} is not "
                        "'<length>' or '<length>@<offset>'"
                    ),
                    line=line,
                    tag=protocol.ext_x_map,
                )
            )
    return diagnostics


def _check_fmp4_maps(data, segments, positions):
    diagnostics = []
    fmp4_context = _has_fmp4_context(data)
    for index, segment in enumerate(segments):
        uri = segment.get("uri")
        requirement = _init_section_requirement(uri, fmp4_context)
        if requirement is None:
            continue
        if segment.get("init_section") is not None:
            # An EXT-X-MAP applies to this segment.
            continue

        position = _position(positions, index)
        line, tag = _segment_location(position, prefer="extinf")
        diagnostics.append(
            Diagnostic(
                rule=RULE_FMP4_MAP_MISSING,
                severity=requirement,
                message=f"fMP4 Media Segment {uri!r} has no applicable EXT-X-MAP",
                line=line,
                tag=tag,
                segment_index=index,
                media_sequence=_segment_sequence(data, index),
                uri=uri,
            )
        )
    return diagnostics


def _has_fmp4_context(data):
    if data.get("segment_map"):
        return True
    version = data.get("version")
    if isinstance(version, bool):
        return False
    try:
        return int(version) >= _EXT_X_MAP_VERSION
    except (TypeError, ValueError):
        return False


def _init_section_requirement(uri, fmp4_context):
    """
    Return the severity for a missing EXT-X-MAP, or None if none is needed.

    ``.m4s`` and friends identify fragmented MP4 unambiguously, so their
    missing initialization section is an error.  ``.mp4`` is ambiguous and
    only counts when the playlist itself shows fMP4 intent, so it is a
    warning: a player may still accept such a segment.
    """
    extension = _file_extension(uri)
    if extension in _FMP4_EXTENSIONS:
        return SEVERITY_ERROR
    if extension in _AMBIGUOUS_FMP4_EXTENSIONS and fmp4_context:
        return SEVERITY_WARNING
    return None


def _check_vod_endlist(data, playlist_type_line):
    playlist_type = data.get("playlist_type")
    if not isinstance(playlist_type, str):
        return []
    if playlist_type.strip().lower() not in _FINAL_PLAYLIST_TYPES:
        return []
    if data.get("is_endlist"):
        return []
    return [
        Diagnostic(
            rule=RULE_VOD_MISSING_ENDLIST,
            severity=SEVERITY_ERROR,
            message=(
                "EXT-X-PLAYLIST-TYPE is VOD but the playlist has no EXT-X-ENDLIST"
            ),
            line=playlist_type_line,
            tag=protocol.ext_x_playlist_type,
        )
    ]


def _segment_location(position, prefer):
    if position is None:
        return None, None

    if prefer == "extinf" and position.extinf_line is not None:
        return position.extinf_line, protocol.extinf
    if position.byterange_line is not None:
        return position.byterange_line, protocol.ext_x_byterange
    if position.extinf_line is not None:
        return position.extinf_line, protocol.extinf
    if position.uri_line is not None:
        return position.uri_line, None
    return None, None


def _file_extension(uri):
    if not uri:
        return ""
    path = uri.split("#", 1)[0].split("?", 1)[0]
    return os.path.splitext(path)[1].lower()


def _diagnostic_sort_key(diagnostic):
    # Playlist order first.  Diagnostics without a line (an object whose
    # source text is gone) keep a deterministic segment order at the end.
    return (
        diagnostic.line is None,
        diagnostic.line if diagnostic.line is not None else 0,
        diagnostic.segment_index if diagnostic.segment_index is not None else -1,
    )
