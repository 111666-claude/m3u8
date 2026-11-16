# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.
"""
Cross-rendition reports for Low-Latency HLS.

In a Low-Latency HLS presentation the video, audio and subtitle renditions
are published as separate Media Playlists that scroll independently.  A
player that has to switch renditions, or that has to align a rendition it
is not currently downloading, needs to know how far the other renditions
have progressed.  For that purpose every Media Playlist carries one
``EXT-X-RENDITION-REPORT`` tag for each *other* Media Playlist, with a URI
relative to the playlist that contains the tag, plus the Media Sequence
Number (and, for Partial Segments, the Part Index) of the last media the
referenced rendition had published when the containing playlist was
generated (RFC 8216bis section 4.4.5.4).

:func:`generate_rendition_reports` builds those tags from the current
snapshot of every track.  The input playlists are updated in place: their
previously published ``EXT-X-RENDITION-REPORT`` tags are replaced as a
whole, while segments, keys, ``EXT-X-PART`` tags and every other tag are
left untouched, so the resulting playlists can be dumped and parsed again
without losing information.
"""

import posixpath
from collections import namedtuple
from collections.abc import Mapping
from urllib.parse import urljoin, urlsplit

from m3u8.model import M3U8, RenditionReport


class RenditionReportError(ValueError):
    """
    Raised when cross-rendition reports cannot be generated safely.

    Publishing a report that points at the wrong Media Sequence Number or
    Part Index is worse than publishing no report at all, so an invalid
    track (empty, expired, with duplicate URIs, without a determinable
    sequence number or with an illegal ``EXT-X-PART`` layout) aborts the
    whole generation before any playlist is modified.
    """


class RenditionTrack:
    """A Media Playlist together with the URI used to publish it."""

    def __init__(self, uri, playlist):
        self.uri = uri
        self.playlist = playlist

    def __repr__(self):
        return "RenditionTrack(uri=%r, playlist=%r)" % (self.uri, self.playlist)


_Position = namedtuple("_Position", ["last_msn", "last_part"])


def generate_rendition_reports(tracks):
    """
    Generate and install cross-rendition reports for a set of tracks.

    ``tracks`` supplies the URI and the current Media Playlist of every
    rendition that takes part in the presentation.  It accepts:

    * a mapping ``{uri: playlist}``;
    * an iterable of ``(uri, playlist)`` pairs;
    * an iterable of :class:`RenditionTrack` objects;
    * an iterable of :class:`~m3u8.model.M3U8` objects that carry a ``uri``
      attribute.

    A ``playlist`` may be an ``M3U8`` object or the playlist content as a
    string.  For every track, one ``EXT-X-RENDITION-REPORT`` tag is
    generated for each *other* track, in input order.  The URI attribute of
    a report is always relative to the URI of the playlist that contains
    the report.  ``LAST-MSN`` is the Media Sequence Number of the last
    Media Segment in the referenced rendition; when that rendition contains
    Partial Segments, ``LAST-MSN`` and ``LAST-PART`` instead identify its
    last Partial Segment, as RFC 8216bis 4.4.5.4 requires.

    Existing reports are replaced (never appended to), so calling this
    function repeatedly with newer snapshots performs a normal playlist
    update.  All tracks are validated before anything is modified; if any
    track is invalid the input playlists are left exactly as they were.

    Returns a ``dict`` mapping each URI to its playlist object, in input
    order.
    """
    track_list = _coerce_tracks(tracks)

    positions = {}
    for track in track_list:
        positions[track.uri] = _playlist_position(track.playlist, track.uri)

    # Build every report list before touching the playlists: a failure in
    # one report (for example a cross-host relative URI) must not leave the
    # other tracks partially updated.
    reports_by_uri = {}
    for source in track_list:
        base_uri = source.playlist.base_uri
        if base_uri is None and urlsplit(source.uri).scheme:
            base_uri = urljoin(source.uri, ".")
        reports = []
        for target in track_list:
            if target is source:
                continue
            position = positions[target.uri]
            reports.append(
                RenditionReport(
                    base_uri=base_uri,
                    uri=_relative_uri(source.uri, target.uri),
                    last_msn=position.last_msn,
                    last_part=position.last_part,
                )
            )
        reports_by_uri[source.uri] = reports

    for track in track_list:
        track.playlist.set_rendition_reports(reports_by_uri[track.uri])

    return {track.uri: track.playlist for track in track_list}


def _coerce_tracks(tracks):
    if isinstance(tracks, Mapping):
        entries = list(tracks.items())
    elif isinstance(tracks, (str, bytes, M3U8)):
        raise RenditionReportError(
            "tracks must be a mapping or an iterable of (uri, playlist) pairs"
        )
    else:
        try:
            entries = list(tracks)
        except TypeError:
            raise RenditionReportError(
                "tracks must be a mapping or an iterable of (uri, playlist) pairs"
            ) from None

    track_list = []
    for entry in entries:
        if isinstance(entry, RenditionTrack):
            uri, playlist = entry.uri, entry.playlist
        elif isinstance(entry, (tuple, list)) and len(entry) == 2:
            uri, playlist = entry
        elif isinstance(entry, M3U8):
            uri, playlist = getattr(entry, "uri", None), entry
        elif hasattr(entry, "uri") and hasattr(entry, "playlist"):
            uri, playlist = entry.uri, entry.playlist
        else:
            raise RenditionReportError(
                "each track must be a (uri, playlist) pair or a RenditionTrack, "
                "got %r" % (entry,)
            )

        if not isinstance(uri, str) or not uri.strip():
            raise RenditionReportError("each track needs a non-empty URI")
        if isinstance(playlist, str):
            playlist = M3U8(playlist)
        if not isinstance(playlist, M3U8):
            raise RenditionReportError(
                "track %r: playlist must be an M3U8 object or playlist content" % uri
            )

        track_list.append(RenditionTrack(uri, playlist))

    if not track_list:
        raise RenditionReportError("at least one track is required")

    identities = {}
    playlist_uris = {}
    for track in track_list:
        identity = _uri_identity(track.uri)
        if identity in identities:
            raise RenditionReportError(
                "duplicate track URI: %r and %r" % (identities[identity], track.uri)
            )
        identities[identity] = track.uri

        existing_uri = playlist_uris.get(id(track.playlist))
        if existing_uri is not None and existing_uri != track.uri:
            raise RenditionReportError(
                "the same playlist object is used for tracks %r and %r"
                % (existing_uri, track.uri)
            )
        playlist_uris[id(track.playlist)] = track.uri

    return track_list


def _uri_identity(uri):
    parts = urlsplit(uri)
    path = parts.path
    if path:
        path = posixpath.normpath(path)
        if path == ".":
            path = ""
    return (parts.scheme.lower(), parts.netloc.lower(), path, parts.query)


def _relative_uri(source_uri, target_uri):
    """Return ``target_uri`` relative to the directory of ``source_uri``."""
    source = urlsplit(source_uri)
    target = urlsplit(target_uri)

    if (
        source.scheme.lower() != target.scheme.lower()
        or source.netloc.lower() != target.netloc.lower()
    ):
        raise RenditionReportError(
            "cannot express track URI %r relative to %r: they belong to "
            "different origins" % (target_uri, source_uri)
        )

    if not target.path:
        raise RenditionReportError("track URI %r has no path" % (target_uri,))
    if target.path.endswith("/"):
        raise RenditionReportError(
            "track URI %r does not name a media playlist" % (target_uri,)
        )
    if source.path.startswith("/") != target.path.startswith("/"):
        raise RenditionReportError(
            "cannot express track URI %r relative to %r: one path is "
            "absolute and the other is not" % (target_uri, source_uri)
        )

    source_dir = posixpath.dirname(source.path)
    if not source_dir:
        relative = target.path
    else:
        relative = posixpath.relpath(target.path, source_dir)

    if target.query:
        relative += "?" + target.query
    if target.fragment:
        relative += "#" + target.fragment
    return relative


def _playlist_position(playlist, uri):
    """
    Return the ``_Position`` advertised for ``playlist``.

    ``LAST-MSN`` is derived from ``EXT-X-MEDIA-SEQUENCE``, the number of
    Media Segments currently listed and, when the playlist is a Playlist
    Delta Update, ``SKIPPED-SEGMENTS``.  The trailing Media Segment (which
    may have no URI yet, in which case it only consists of Partial
    Segments) determines ``LAST-PART``.
    """
    label = "track %r" % (uri,)

    if playlist.is_variant:
        raise RenditionReportError(
            "%s is a master playlist; rendition reports need a media playlist" % label
        )

    segments = list(playlist.segments)
    if not segments:
        raise RenditionReportError(
            "%s is empty or expired: it lists no Media Segment and no "
            "Partial Segment" % label
        )

    skipped_segments = 0
    skip = getattr(playlist, "skip", None)
    if skip:
        skipped_segments = getattr(skip, "skipped_segments", 0)
        if (
            not isinstance(skipped_segments, int)
            or isinstance(skipped_segments, bool)
            or skipped_segments < 0
        ):
            raise RenditionReportError(
                "%s has an invalid EXT-X-SKIP SKIPPED-SEGMENTS value %r"
                % (label, skipped_segments)
            )

    media_sequence = playlist.media_sequence
    if media_sequence is None:
        # Playlists built by hand may not carry EXT-X-MEDIA-SEQUENCE; fall
        # back to the per-segment numbers when every segment has one.
        if skipped_segments:
            raise RenditionReportError(
                "%s has no EXT-X-MEDIA-SEQUENCE, so LAST-MSN cannot be "
                "determined for a Playlist Delta Update" % label
            )
        segment_msns = [
            getattr(segment, "media_sequence", None) for segment in segments
        ]
        if not all(
            isinstance(msn, int) and not isinstance(msn, bool) for msn in segment_msns
        ):
            raise RenditionReportError(
                "%s has no EXT-X-MEDIA-SEQUENCE and its Media Segment "
                "numbers cannot be determined" % label
            )
        for index in range(1, len(segment_msns)):
            if segment_msns[index] != segment_msns[index - 1] + 1:
                raise RenditionReportError(
                    "%s has non-consecutive Media Segment numbers; "
                    "LAST-MSN cannot be determined" % label
                )
        first_listed_msn = segment_msns[0]
    else:
        if not isinstance(media_sequence, int) or isinstance(media_sequence, bool):
            raise RenditionReportError(
                "%s has an invalid EXT-X-MEDIA-SEQUENCE %r" % (label, media_sequence)
            )
        # In a Playlist Delta Update EXT-X-MEDIA-SEQUENCE is the sequence
        # number of the oldest Media Segment of the window, whose tags were
        # replaced by EXT-X-SKIP; the first segment listed in the file
        # comes SKIPPED-SEGMENTS later.
        first_listed_msn = media_sequence + skipped_segments

    for index, segment in enumerate(segments):
        parts = getattr(segment, "parts", None) or []
        if segment.uri is None:
            if not parts:
                raise RenditionReportError(
                    "%s is empty or expired: its last segment has neither a "
                    "URI nor Partial Segments" % label
                )
            if index != len(segments) - 1:
                raise RenditionReportError(
                    "%s has an illegal PART layout: only the last Media "
                    "Segment may omit its URI" % label
                )
        for part_index, part in enumerate(parts):
            if not getattr(part, "uri", None):
                raise RenditionReportError(
                    "%s has an illegal PART layout: Partial Segment %d of "
                    "Media Sequence Number %d has no URI"
                    % (label, part_index, first_listed_msn + index)
                )

    last_msn = first_listed_msn + len(segments) - 1

    # RFC 8216bis 4.4.5.4: when the rendition contains Partial Segments,
    # LAST-MSN is the Media Sequence Number of the last Partial Segment and
    # LAST-PART is that Partial Segment's Part Index. In a live rendition
    # those belong to the trailing, not yet completed Media Segment, which
    # is also the media a player can fetch last.
    trailing_parts = getattr(segments[-1], "parts", None) or []
    last_part = len(trailing_parts) - 1 if trailing_parts else None

    return _Position(last_msn=last_msn, last_part=last_part)
