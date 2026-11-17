# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.
"""
A gap aware timeline of the segments of a Media Playlist.

A player builds its playback timeline from the ``EXTINF`` durations of the
Media Segments in a Media Playlist.  A segment tagged with ``EXT-X-GAP`` is
not available (``EXT-X-GAP`` is how a publisher says "this piece of the
timeline exists, but do not request its URI"), yet it still occupies its
``EXTINF`` duration: the following segments must keep their original
positions, otherwise audio/video/subtitle renditions would drift apart.

This module turns a parsed playlist into :class:`SegmentTimeline` objects
that make that distinction explicit.  Every :class:`TimelineEntry` carries
the Media Sequence Number, URI, start/end offsets and a ``downloadable``
flag; gap segments occupy their duration but are never downloadable.  When
``EXT-X-PROGRAM-DATE-TIME`` anchors are present, entries also carry
wall-clock ``start_date``/``end_date`` values.  Entries are grouped into
:class:`TimelineSpan` objects: a new span starts at every
``EXT-X-DISCONTINUITY``, because the media timeline is not continuous
across one.

Building a timeline never modifies the playlist nor its segments: entries
keep a reference to the original :class:`~m3u8.model.Segment` objects and
``playlist.dumps()`` is unaffected.
"""

from __future__ import annotations

import dataclasses
import math
import numbers
from datetime import datetime, timedelta

from m3u8.model import Segment


class TimelineError(ValueError):
    """
    Raised when a gap aware timeline cannot be built safely.

    A timeline that silently invents time would make a player request the
    wrong segments, so the following are rejected instead of guessed:
    playlists without segments, segments without a valid ``EXTINF``
    duration, duplicate or non-contiguous Media Sequence Numbers,
    ``EXT-X-PROGRAM-DATE-TIME`` values that go backwards inside a
    continuous span, and gap segments whose duration or partial segments
    contradict the fact that the whole segment is unavailable.
    """


@dataclasses.dataclass(frozen=True)
class TimelineEntry:
    """
    One Media Segment on the playback timeline.

    `media_sequence`
      Media Sequence Number of the segment

    `uri`
      URI of the segment as published by the playlist (the URI of a gap
      segment is kept for reference but must not be requested)

    `duration`
      ``EXTINF`` duration in seconds

    `start`, `end`
      offsets in seconds from the start of the timeline.  They are
      cumulative over every segment, gaps included, and continue across
      discontinuities, so the timeline positions never collapse when a
      segment is missing.

    `downloadable`
      ``True`` only when the segment can actually be requested: it has a
      URI and is not a gap.  A segment without a URI (a partially
      published Low-Latency segment) is not downloadable either.

    `gap`
      ``True`` when the segment is tagged ``EXT-X-GAP``

    `discontinuity`
      ``True`` when the segment is preceded by ``EXT-X-DISCONTINUITY``

    `segment`
      the original :class:`~m3u8.model.Segment` object, so callers keep
      access to keys, partial segments, titles, etc.

    `program_date_time`
      the explicit ``EXT-X-PROGRAM-DATE-TIME`` anchor of this segment, if
      any

    `start_date`, `end_date`
      wall-clock start/end of the segment, when a
      ``EXT-X-PROGRAM-DATE-TIME`` anchor is known for it or for an earlier
      segment of the same continuous span.  They are ``None`` otherwise.
    """

    media_sequence: int
    uri: str | None
    duration: float
    start: float
    end: float
    downloadable: bool
    gap: bool
    discontinuity: bool
    segment: Segment = dataclasses.field(compare=False, repr=False)
    program_date_time: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None

    def to_dict(self):
        """
        Returns the entry as a plain, JSON-friendly dict.

        The original ``segment`` object is not included.  Dates are
        rendered as ISO 8601 strings and stay ``None`` when the timeline
        has no anchor for the entry.
        """
        return {
            "media_sequence": self.media_sequence,
            "uri": self.uri,
            "duration": self.duration,
            "start": self.start,
            "end": self.end,
            "downloadable": self.downloadable,
            "gap": self.gap,
            "discontinuity": self.discontinuity,
            "program_date_time": _isoformat(self.program_date_time),
            "start_date": _isoformat(self.start_date),
            "end_date": _isoformat(self.end_date),
        }


@dataclasses.dataclass(frozen=True)
class TimelineSpan:
    """
    A continuous run of timeline entries, between two discontinuities.

    The first entry of a span may be flagged ``discontinuity``; the
    relative offsets still continue from the previous span (they measure
    elapsed playlist time), while wall-clock dates are only continuous
    inside a span.
    """

    entries: tuple[TimelineEntry, ...]

    def __post_init__(self):
        if not self.entries:
            raise TimelineError("a timeline span needs at least one entry")

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def __getitem__(self, index):
        return self.entries[index]

    @property
    def start(self):
        return self.entries[0].start

    @property
    def end(self):
        return self.entries[-1].end

    @property
    def duration(self):
        return self.end - self.start

    @property
    def start_date(self):
        return self.entries[0].start_date

    @property
    def end_date(self):
        return self.entries[-1].end_date

    @property
    def start_media_sequence(self):
        return self.entries[0].media_sequence

    @property
    def end_media_sequence(self):
        return self.entries[-1].media_sequence

    @property
    def media_sequences(self):
        return tuple(entry.media_sequence for entry in self.entries)

    @property
    def downloadable_duration(self):
        return sum(entry.duration for entry in self.entries if entry.downloadable)

    @property
    def gap_duration(self):
        return sum(entry.duration for entry in self.entries if entry.gap)


@dataclasses.dataclass(frozen=True)
class SegmentTimeline:
    """
    A gap aware timeline of a Media Playlist, ordered by Media Sequence
    Number.

    `entries`
      every Media Segment, in playlist order

    `spans`
      the entries grouped into continuous runs; a discontinuity starts a
      new span

    Iterating the timeline yields :class:`TimelineEntry` objects in Media
    Sequence order.  Use :meth:`entry` to look one up by Media Sequence
    Number and :meth:`next_downloadable` to skip over gaps while moving
    forward.
    """

    entries: tuple[TimelineEntry, ...]
    spans: tuple[TimelineSpan, ...]

    def __post_init__(self):
        if not self.entries:
            raise TimelineError("a segment timeline needs at least one entry")

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def __getitem__(self, index):
        return self.entries[index]

    @property
    def start(self):
        return self.entries[0].start

    @property
    def end(self):
        return self.entries[-1].end

    @property
    def duration(self):
        return self.end - self.start

    @property
    def start_date(self):
        return self.entries[0].start_date

    @property
    def end_date(self):
        return self.entries[-1].end_date

    @property
    def media_sequences(self):
        return tuple(entry.media_sequence for entry in self.entries)

    @property
    def by_media_sequence(self):
        return {entry.media_sequence: entry for entry in self.entries}

    @property
    def downloadable_entries(self):
        return tuple(entry for entry in self.entries if entry.downloadable)

    @property
    def gap_entries(self):
        return tuple(entry for entry in self.entries if entry.gap)

    @property
    def downloadable_duration(self):
        return sum(entry.duration for entry in self.entries if entry.downloadable)

    @property
    def gap_duration(self):
        return sum(entry.duration for entry in self.entries if entry.gap)

    def entry(self, media_sequence):
        """
        Returns the entry with the given Media Sequence Number.

        :raises KeyError: when the timeline has no such segment.
        """
        for entry in self.entries:
            if entry.media_sequence == media_sequence:
                return entry
        raise KeyError(
            "media sequence number %r is not on the timeline" % (media_sequence,)
        )

    def next_downloadable(self, media_sequence):
        """
        Returns the first downloadable entry after `media_sequence`
        (exclusive), skipping gaps and partial-only segments.

        Returns ``None`` when no downloadable segment follows, so a player
        can loop over the timeline without special casing the tail.
        """
        for entry in self.entries:
            if entry.media_sequence > media_sequence and entry.downloadable:
                return entry
        return None

    def to_dicts(self):
        """
        Returns the whole timeline as a list of plain dicts, one per
        segment, in Media Sequence order (media sequence, URI, start/end,
        ``downloadable`` and the dates when known).
        """
        return [entry.to_dict() for entry in self.entries]


def build_segment_timeline(playlist):
    """
    Builds a gap aware :class:`SegmentTimeline` for a Media Playlist.

    `playlist` is an :class:`~m3u8.model.M3U8` object.  Master playlists
    and playlists without segments are rejected.  The playlist is not
    modified; the returned entries reference its original ``Segment``
    objects.

    Timing rules:

    * every segment must have a finite, non-negative ``EXTINF`` duration;
      gap segments must have a positive one, because a gap that occupies
      no time cannot hold its place on the timeline;
    * offsets are cumulative over all segments, including gaps;
    * ``EXT-X-PROGRAM-DATE-TIME`` anchors define wall-clock dates and are
      used as-is.  Within a continuous span a later anchor may not be
      earlier than the timestamp implied by the preceding segments; an
      anchor that moves forward simply rebases the following dates;
    * an ``EXT-X-DISCONTINUITY`` starts a new :class:`TimelineSpan` and
      ends wall-clock continuity.  A new anchor may therefore move in any
      direction; without one, ``start_date``/``end_date`` stay ``None``
      until the next anchor;
    * Media Sequence Numbers must be unique and contiguous.  A duplicated
      or skipped number cannot be placed on the timeline deterministically.

    A gap segment is never downloadable.  Its duration is still part of
    the timeline and of its span.  A gap segment carrying at least one
    playable (non-gap) partial segment is rejected as contradictory; when
    all of its partial segments are gaps, the parent gap wins and the
    segment stays non-downloadable.

    :raises TimelineError: for the invalid playlists described above.
    """
    if getattr(playlist, "is_variant", False):
        raise TimelineError(
            "a segment timeline requires a Media Playlist, not a Master Playlist"
        )

    segments = getattr(playlist, "segments", None)
    if not segments:
        raise TimelineError("a segment timeline requires a playlist with segments")

    entries = []
    seen_media_sequences = set()
    expected_media_sequence = None
    offset = 0.0
    current_date = None

    for position, segment in enumerate(segments):
        media_sequence = _media_sequence_of(playlist, segment, position)
        if media_sequence in seen_media_sequences:
            raise TimelineError(
                "duplicate media sequence number %d at segment #%d"
                % (media_sequence, position)
            )
        if (
            expected_media_sequence is not None
            and media_sequence != expected_media_sequence
        ):
            raise TimelineError(
                "media sequence numbers are not contiguous: expected %d after "
                "segment #%d but found %d"
                % (expected_media_sequence, position - 1, media_sequence)
            )
        seen_media_sequences.add(media_sequence)
        expected_media_sequence = media_sequence + 1

        duration = _duration_of(segment, position, media_sequence)
        gap = _is_gap(segment, position, media_sequence)
        if gap and duration <= 0:
            raise TimelineError(
                "gap segment at media sequence number %d must have a positive "
                "EXTINF duration, got %r" % (media_sequence, segment.duration)
            )
        _validate_gap_parts(segment, position, media_sequence)

        discontinuity = bool(getattr(segment, "discontinuity", False))
        if discontinuity:
            # Wall-clock time is not continuous across a discontinuity.
            current_date = None

        anchor = getattr(segment, "program_date_time", None)
        if anchor is not None:
            if not isinstance(anchor, datetime):
                raise TimelineError(
                    "segment at media sequence number %d has an invalid "
                    "PROGRAM-DATE-TIME anchor: %r" % (media_sequence, anchor)
                )
            if current_date is not None:
                try:
                    backwards = anchor < current_date
                except TypeError:
                    raise TimelineError(
                        "PROGRAM-DATE-TIME at media sequence number %d mixes "
                        "timezone-aware and naive datetimes" % media_sequence
                    ) from None
                if backwards:
                    raise TimelineError(
                        "PROGRAM-DATE-TIME at media sequence number %d goes "
                        "backwards: %s is earlier than the expected %s"
                        % (
                            media_sequence,
                            anchor.isoformat(),
                            current_date.isoformat(),
                        )
                    )
            start_date = anchor
        else:
            start_date = current_date

        end_date = (
            start_date + timedelta(seconds=duration) if start_date is not None else None
        )
        downloadable = (not gap) and bool(segment.uri)

        entries.append(
            TimelineEntry(
                media_sequence=media_sequence,
                uri=segment.uri,
                duration=duration,
                start=offset,
                end=offset + duration,
                downloadable=downloadable,
                gap=gap,
                discontinuity=discontinuity,
                segment=segment,
                program_date_time=anchor,
                start_date=start_date,
                end_date=end_date,
            )
        )
        offset += duration
        current_date = end_date

    return SegmentTimeline(
        entries=tuple(entries),
        spans=_group_into_spans(entries),
    )


def _media_sequence_of(playlist, segment, position):
    media_sequence = getattr(segment, "media_sequence", None)
    if media_sequence is None:
        base = getattr(playlist, "media_sequence", 0)
        if not _is_integer(base):
            base = 0
        media_sequence = base + position
    if not _is_integer(media_sequence):
        raise TimelineError(
            "segment #%d has an invalid media sequence number: %r"
            % (position, media_sequence)
        )
    return media_sequence


def _duration_of(segment, position, media_sequence):
    duration = segment.duration
    if duration is None:
        raise TimelineError(
            "segment #%d (media sequence number %d) has no EXTINF duration"
            % (position, media_sequence)
        )
    if isinstance(duration, bool) or not isinstance(duration, numbers.Real):
        raise TimelineError(
            "segment #%d (media sequence number %d) has a non-numeric EXTINF "
            "duration: %r" % (position, media_sequence, duration)
        )
    value = float(duration)
    if not math.isfinite(value) or value < 0:
        raise TimelineError(
            "segment #%d (media sequence number %d) has an invalid EXTINF "
            "duration: %r" % (position, media_sequence, duration)
        )
    return value


def _is_gap(segment, position, media_sequence):
    gap_tag = getattr(segment, "gap_tag", None)
    if gap_tag is not None and not isinstance(gap_tag, bool):
        raise TimelineError(
            "segment #%d (media sequence number %d) has an invalid EXT-X-GAP "
            "value: %r" % (position, media_sequence, gap_tag)
        )
    return bool(gap_tag)


def _validate_gap_parts(segment, position, media_sequence):
    if not getattr(segment, "gap_tag", False):
        return
    playable_parts = [
        part for part in getattr(segment, "parts", ()) if not _is_gap_part(part)
    ]
    if playable_parts:
        raise TimelineError(
            "gap segment #%d (media sequence number %d) carries downloadable "
            "partial segments; a gap cannot be both unavailable and playable"
            % (position, media_sequence)
        )


def _is_gap_part(part):
    if getattr(part, "gap_tag", None) is True:
        return True
    gap = getattr(part, "gap", None)
    if isinstance(gap, str):
        return gap.strip().upper() == "YES"
    return gap is True


def _group_into_spans(entries):
    spans = []
    current = []
    for entry in entries:
        if entry.discontinuity and current:
            spans.append(TimelineSpan(entries=tuple(current)))
            current = []
        current.append(entry)
    if current:
        spans.append(TimelineSpan(entries=tuple(current)))
    return tuple(spans)


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _isoformat(value):
    return value.isoformat() if value is not None else None
