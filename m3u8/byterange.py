# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

"""
Resolution of EXT-X-BYTERANGE values into absolute HTTP byte ranges.

Live streams frequently split a single media file into several segments
(or partial segments) that only carry a length in their BYTERANGE tag,
relying on the implicit rule that an omitted offset continues right after
the previous sub-range of the *same* resource.  This module walks a
playlist in order and turns every BYTERANGE found on Segments,
PartialSegments and InitializationSections into a closed-interval HTTP
Range plus its length.

Inheritance rules (RFC 8216 section 4.3.2.2):

* an omitted offset inherits the end of the previous byte range of the
  same resource URI only;
* initialization sections (EXT-X-MAP) are independent resources: they
  never inherit from media segments and media segments never inherit
  from them;
* an entry without a BYTERANGE addresses the whole file: it neither
  inherits nor may serve as inheritance predecessor, so it resets the
  cursor for its URI.
"""

import re
from collections import namedtuple

#: Largest byte offset/length representable in a signed 64-bit integer.
MAX_INT64 = 2**63 - 1

_BYTERANGE_RE = re.compile(r"^(-?\d+)(?:@(-?\d+))?$")


class ByteRangeError(ValueError):
    """
    Raised when the BYTERANGE values of a playlist cannot be resolved.

    `sequence`
      media sequence number of the segment holding the offending tag

    `kind`
      one of "segment", "part" or "map"
    """

    def __init__(self, reason, sequence=None, kind=None):
        self.reason = reason
        self.sequence = sequence
        self.kind = kind
        where = []
        if kind:
            where.append(kind)
        if sequence is not None:
            where.append("of segment %s" % sequence)
        location = " ".join(where) if where else "playlist"
        super().__init__("invalid BYTERANGE on %s: %s" % (location, reason))


class ByteRangeRef(
    namedtuple("ByteRangeRef", ["sequence", "kind", "uri", "start", "end", "length"])
):
    """
    A resolved byte range of a playlist resource.

    `sequence`
      media sequence number of the segment the range belongs to

    `kind`
      "segment", "part" or "map"

    `uri`
      the resource URI the range applies to

    `start` / `end`
      closed (inclusive) byte interval, as used by the HTTP Range header

    `length`
      number of bytes in the range (end - start + 1)
    """

    __slots__ = ()

    @property
    def http_range(self):
        """Value for an HTTP `Range` request header, e.g. ``bytes=0-99``."""
        return "bytes=%d-%d" % (self.start, self.end)


def parse_byterange(value, sequence=None, kind=None):
    """
    Parse a ``<length>[@<offset>]`` BYTERANGE value.

    Returns a ``(offset, length)`` tuple where `offset` is None when the
    value omits it.  Raises ByteRangeError on malformed values,
    non-positive lengths and 64-bit overflows.
    """
    text = value.strip() if isinstance(value, str) else ""
    match = _BYTERANGE_RE.match(text)
    if not match:
        raise ByteRangeError("malformed value %r" % (value,), sequence, kind)

    length = int(match.group(1))
    offset = match.group(2)
    offset = int(offset) if offset is not None else None

    if length <= 0:
        raise ByteRangeError(
            "non-positive length %d in %r" % (length, value), sequence, kind
        )
    if length > MAX_INT64:
        raise ByteRangeError(
            "length %d overflows 64-bit range" % length, sequence, kind
        )
    if offset is not None:
        if offset < 0:
            raise ByteRangeError(
                "negative offset %d in %r" % (offset, value), sequence, kind
            )
        if offset > MAX_INT64:
            raise ByteRangeError(
                "offset %d overflows 64-bit range" % offset, sequence, kind
            )
        if offset + length - 1 > MAX_INT64:
            raise ByteRangeError(
                "range %r end overflows 64-bit range" % (value,), sequence, kind
            )
    return offset, length


def _resolve_entry(cursors, refs, uri, byterange, sequence, kind):
    if byterange is None:
        # Whole-file entry: not a sub-range, so it must not act as the
        # inheritance predecessor for a later offset-less BYTERANGE.
        cursors.pop(uri, None)
        return

    offset, length = parse_byterange(byterange, sequence, kind)
    previous_end = cursors.get(uri)

    if offset is None:
        if previous_end is None:
            raise ByteRangeError(
                "offset omitted but no previous range of resource %r" % uri,
                sequence,
                kind,
            )
        offset = previous_end
    elif previous_end is not None and offset < previous_end:
        raise ByteRangeError(
            "range %r overlaps previous range of resource %r ending at %d"
            % (byterange, uri, previous_end - 1),
            sequence,
            kind,
        )

    end = offset + length - 1
    if end > MAX_INT64:
        raise ByteRangeError(
            "range %r end overflows 64-bit range" % (byterange,), sequence, kind
        )

    refs.append(ByteRangeRef(sequence, kind, uri, offset, end, length))
    cursors[uri] = end + 1


def resolve_byteranges(segments):
    """
    Resolve the BYTERANGE of every Segment, PartialSegment and
    InitializationSection, in playlist order.

    `segments`
      an iterable of Segment objects (e.g. ``M3U8.segments``)

    Returns a list of ByteRangeRef, one per BYTERANGE-carrying entry, in
    playlist order.  Raises ByteRangeError — carrying the media sequence
    number of the offending segment — on zero/negative lengths, omitted
    offsets without a predecessor on the same resource, overlapping
    ranges, malformed values and 64-bit overflows.
    """
    refs = []
    media_cursors = {}  # segments and their parts share one resource chain
    map_cursors = {}  # initialization sections are independent resources
    last_init_section = None

    for index, segment in enumerate(segments):
        sequence = segment.media_sequence
        if sequence is None:
            sequence = index

        init_section = getattr(segment, "init_section", None)
        if init_section is not None and init_section.uri:
            # the model repeats the current EXT-X-MAP on every segment it
            # applies to; only the first occurrence is a playlist entry
            if init_section != last_init_section:
                _resolve_entry(
                    map_cursors,
                    refs,
                    init_section.uri,
                    init_section.byterange,
                    sequence,
                    "map",
                )
            last_init_section = init_section
        else:
            last_init_section = None

        for part in getattr(segment, "parts", None) or []:
            if part.uri:
                _resolve_entry(
                    media_cursors, refs, part.uri, part.byterange, sequence, "part"
                )

        if segment.uri:
            _resolve_entry(
                media_cursors, refs, segment.uri, segment.byterange, sequence, "segment"
            )

    return refs
