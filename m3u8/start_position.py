# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

"""Resolve an EXT-X-START tag into the first segment a player should download.

The main entry point is `resolve_start`. It takes a parsed `M3U8` object and
turns the TIME-OFFSET / PRECISE attributes of its EXT-X-START tag into a
concrete `StartPosition`: which segment to download, how far into that
segment the playback point falls (and, for low-latency playlists, which
partial segment), and the absolute landing time measured from the start of
the playlist window.

The resolution is a pure read of the playlist: nothing on the `M3U8`
object, its segments or its parts is modified.
"""

import math

# Tolerance used when snapping a landing point to a segment boundary, to
# absorb floating point noise from accumulating EXTINF durations.
BOUNDARY_EPSILON = 1e-9


class StartPositionError(Exception):
    """Base class for EXT-X-START resolution failures."""


class MissingStartTagError(StartPositionError):
    """The playlist has no EXT-X-START tag and no offset was provided."""


class InvalidStartOffsetError(StartPositionError, ValueError):
    """TIME-OFFSET is not a finite number."""


class NoPlayableSegmentsError(StartPositionError):
    """The playlist window contains no downloadable (non-GAP) segment."""


class MissingDurationError(StartPositionError, ValueError):
    """A segment has no EXTINF duration and no parts to derive it from."""


class StartPosition:
    """The resolved playback start point.

    `segment`
      the `Segment` the player should download first

    `segment_index`
      index of `segment` in `playlist.segments`

    `offset`
      offset in seconds from the start of `segment` to the playback point

    `position`
      absolute landing time in seconds, measured from the start of the
      playlist window (after clamping and GAP skips)

    `part` / `part_index` / `part_offset`
      for low-latency playlists, the `PartialSegment` containing the
      playback point, its index in `segment.parts` and the offset into it.
      `None` when the target segment lists no parts.

    `time_offset`
      the TIME-OFFSET value that was resolved

    `precise`
      whether PRECISE=YES was requested

    `clamped`
      `None` when TIME-OFFSET landed inside the window, `"start"` when a
      negative offset reached before the first segment, `"end"` when a
      positive offset reached past the last one

    `at_boundary`
      True when the landing point sits exactly on a segment boundary. The
      boundary is attributed to the segment that *starts* there (offset 0),
      except at the very end of the playlist, where it is attributed to the
      end of the last segment.

    `skipped_gaps`
      number of GAP segments skipped while moving the landing point forward
      to the next downloadable segment

    `discontinuities_crossed`
      number of EXT-X-DISCONTINUITY tags before the target segment
    """

    def __init__(
        self,
        segment,
        segment_index,
        offset,
        position,
        part=None,
        part_index=None,
        part_offset=None,
        time_offset=0.0,
        precise=False,
        clamped=None,
        at_boundary=False,
        skipped_gaps=0,
        discontinuities_crossed=0,
    ):
        self.segment = segment
        self.segment_index = segment_index
        self.offset = offset
        self.position = position
        self.part = part
        self.part_index = part_index
        self.part_offset = part_offset
        self.time_offset = time_offset
        self.precise = precise
        self.clamped = clamped
        self.at_boundary = at_boundary
        self.skipped_gaps = skipped_gaps
        self.discontinuities_crossed = discontinuities_crossed

    def __repr__(self):
        return (
            "StartPosition(segment_index=%r, offset=%r, position=%r, "
            "part_index=%r, part_offset=%r, clamped=%r, at_boundary=%r, "
            "skipped_gaps=%r)"
            % (
                self.segment_index,
                self.offset,
                self.position,
                self.part_index,
                self.part_offset,
                self.clamped,
                self.at_boundary,
                self.skipped_gaps,
            )
        )


class _TimelineEntry:
    __slots__ = ("segment", "index", "start", "duration", "playable")

    def __init__(self, segment, index, start, duration, playable):
        self.segment = segment
        self.index = index
        self.start = start
        self.duration = duration
        self.playable = playable


def resolve_start(playlist, time_offset=None, precise=None):
    """Resolve the EXT-X-START tag of `playlist` into a `StartPosition`.

    `time_offset` and `precise` default to the values of the playlist's
    EXT-X-START tag; passing them explicitly overrides the tag (useful for
    playlists that do not carry one, e.g. when a player applies its own
    default live-edge offset).

    Positive offsets are measured from the start of the playlist window,
    negative ones from its end (the live edge for sliding windows), per
    RFC 8216 section 4.3.5.2. Offsets outside the window are clamped and
    reported through `StartPosition.clamped`.

    Raises `MissingStartTagError`, `InvalidStartOffsetError`,
    `NoPlayableSegmentsError` or `MissingDurationError` when the playlist
    cannot yield a download target.
    """
    start = playlist.start
    if time_offset is None:
        if start is None:
            raise MissingStartTagError(
                "playlist has no EXT-X-START tag; pass time_offset explicitly"
            )
        time_offset = start.time_offset
    if precise is None:
        precise = start.precise if start is not None else False
    offset = _validate_offset(time_offset)
    precise = _normalize_precise(precise)

    entries = _build_timeline(playlist)
    if not entries:
        raise NoPlayableSegmentsError("playlist has no media segments")
    if not any(entry.playable for entry in entries):
        raise NoPlayableSegmentsError("playlist has no playable (non-GAP) segments")

    total = entries[-1].start + entries[-1].duration

    target = offset if offset >= 0 else total + offset
    clamped = None
    if target < 0:
        if target < -BOUNDARY_EPSILON:
            clamped = "start"
        target = 0.0
    if target > total:
        if target > total + BOUNDARY_EPSILON:
            clamped = "end"
        target = total

    entry, entry_offset, at_boundary = _locate_entry(entries, target)

    skipped_gaps = 0
    if not entry.playable:
        entry, entry_offset, skipped_gaps, clamped = _skip_gap_segments(
            entries, entry, clamped
        )

    part = part_index = part_offset = None
    parts = entry.segment.parts
    if parts:
        part, part_index, part_offset = _locate_part(parts, entry_offset)
        if part.gap:
            advanced = _skip_gap_parts(parts, part_index)
            if advanced is not None:
                part, part_index, part_offset = advanced
            elif entry.segment.uri is None:
                # A partial segment whose remaining parts are all GAPs has
                # nothing downloadable: move to the next playable segment.
                following = _next_playable(entries, entry)
                if following is None:
                    raise NoPlayableSegmentsError(
                        "no playable segment at or after the start position"
                    )
                entry, entry_offset = following, 0.0
                part = part_index = part_offset = None
            # Otherwise the full segment is still downloadable as a whole.

    return StartPosition(
        segment=entry.segment,
        segment_index=entry.index,
        offset=entry_offset,
        position=entry.start + entry_offset,
        part=part,
        part_index=part_index,
        part_offset=part_offset,
        time_offset=offset,
        precise=precise,
        clamped=clamped,
        at_boundary=at_boundary,
        skipped_gaps=skipped_gaps,
        discontinuities_crossed=sum(
            1 for e in entries if e.index <= entry.index and e.segment.discontinuity
        ),
    )


def _validate_offset(time_offset):
    try:
        offset = float(time_offset)
    except (TypeError, ValueError):
        raise InvalidStartOffsetError(
            "TIME-OFFSET must be a number, got %r" % (time_offset,)
        ) from None
    if not math.isfinite(offset):
        raise InvalidStartOffsetError(
            "TIME-OFFSET must be finite, got %r" % (time_offset,)
        )
    return offset


def _normalize_precise(precise):
    if isinstance(precise, str):
        return precise.upper() == "YES"
    return bool(precise)


def _is_playable(segment):
    if segment.gap_tag:
        return False
    if segment.uri is not None:
        return True
    # Partial (not yet complete) low-latency segment: downloadable through
    # its parts, unless every part is a GAP.
    return any(not part.gap for part in segment.parts)


def _build_timeline(playlist):
    entries = []
    position = 0.0
    for index, segment in enumerate(playlist.segments):
        if segment.uri is None and not segment.parts:
            # Placeholder for a segment that has not been published yet
            # (e.g. EXT-X-PART tags were announced but none delivered).
            continue
        duration = segment.duration
        if duration is None:
            if segment.parts:
                duration = sum(part.duration for part in segment.parts)
            else:
                raise MissingDurationError(
                    "segment %d (%r) has no EXTINF duration and no parts"
                    % (index, segment.uri)
                )
        entries.append(
            _TimelineEntry(segment, index, position, duration, _is_playable(segment))
        )
        position += duration
    return entries


def _locate_entry(entries, target):
    """Find the entry containing `target`.

    A target landing exactly on a boundary is attributed to the entry that
    starts there; a target at the very end of the timeline is attributed to
    the end of the last entry.
    """
    for entry in entries:
        end = entry.start + entry.duration
        if target < end - BOUNDARY_EPSILON:
            offset = target - entry.start
            at_boundary = offset <= BOUNDARY_EPSILON
            if at_boundary:
                offset = 0.0
            return entry, offset, at_boundary
    last = entries[-1]
    return last, last.duration, True


def _skip_gap_segments(entries, entry, clamped):
    """Move a landing point inside a GAP segment to a playable segment.

    Prefers the next playable segment (playback moves forward); falls back
    to the end of the previous playable one when the GAP runs to the end of
    the window. Returns (entry, offset, skipped_gaps, clamped).
    """
    index = entries.index(entry)
    skipped = 0
    for candidate in entries[index:]:
        if candidate.playable:
            return candidate, 0.0, skipped, clamped
        skipped += 1
    for candidate in reversed(entries[:index]):
        if candidate.playable:
            return candidate, candidate.duration, skipped, clamped or "end"
    raise NoPlayableSegmentsError(  # pragma: no cover - guarded by caller
        "playlist has no playable (non-GAP) segments"
    )


def _next_playable(entries, entry):
    index = entries.index(entry)
    for candidate in entries[index + 1 :]:
        if candidate.playable:
            return candidate
    return None


def _locate_part(parts, offset):
    """Return (part, index, part_offset) for an intra-segment offset."""
    start = 0.0
    for index, part in enumerate(parts):
        end = start + part.duration
        if offset < end - BOUNDARY_EPSILON:
            return part, index, offset - start
        start = end
    # The offset points past the listed parts (the segment is still being
    # produced): attribute it to the end of the last known part.
    last = parts[-1]
    return last, len(parts) - 1, last.duration


def _skip_gap_parts(parts, index):
    """Return (part, index, 0.0) for the next non-GAP part, or None."""
    for following in range(index + 1, len(parts)):
        if not parts[following].gap:
            return parts[following], following, 0.0
    return None
