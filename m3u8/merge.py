# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.
"""
Playlist Delta Update merging.

A live client receives a full Media Playlist first and, on later reloads,
may receive a Playlist Delta Update in which the older Media Segments were
replaced by a single ``EXT-X-SKIP`` tag (see section 6.2.5.1 / 6.3.7 of the
HLS 2nd Edition draft).  A delta playlist is not directly usable: the
skipped segments still belong to the current playback window and have to be
restored from the previously known version of the playlist.

The :func:`merge` function combines the previous, complete playlist with
the newly fetched playlist and returns a new, serializable :class:`M3U8`
object holding the up-to-date Media Playlist window.  The two input
playlists are never modified.
"""

import copy

from m3u8.model import (
    M3U8,
    DateRangeList,
    MediaList,
    PartialSegmentList,
    PlaylistList,
    RenditionReportList,
    SegmentList,
    SessionDataList,
)


class PlaylistMergeError(Exception):
    """
    Raised when a playlist cannot be merged into the previous one.

    This happens, for example, when the previous playlist does not contain
    all the segments that an ``EXT-X-SKIP`` tag replaced, when the Media
    Sequence Number moves backwards, or when the playlists are not
    compatible Media Playlists of the same rendition.
    """


def merge(previous, current):
    """
    Merge two consecutive versions of the same Media Playlist.

    ``previous`` is the last complete (or already merged) playlist known by
    the client and ``current`` is the playlist just fetched, which may
    contain an ``EXT-X-SKIP`` tag.  Segments are reconciled by their Media
    Sequence Number:

    * Media Segments replaced by ``EXT-X-SKIP`` and still inside the
      current window are restored from ``previous``;
    * segments present in both playlists are taken from ``current``, so
      updated low-latency Partial Segments (``EXT-X-PART``) are never
      duplicated;
    * segments older than the first segment of ``current`` are cropped out
      of the window;
    * ``EXT-X-KEY``, ``EXT-X-MAP``, the discontinuity sequence,
      ``EXT-X-PROGRAM-DATE-TIME`` and ``EXT-X-DATERANGE`` information
      attached to the surviving segments is preserved.

    A new :class:`~m3u8.model.M3U8` object is returned; neither input
    playlist is modified.

    :raises PlaylistMergeError: if the playlists are not Media Playlists,
        their history does not cover the skipped segments, their sequence
        numbers move backwards, or they are otherwise incompatible.
    """
    _ensure_media_playlist(previous, "previous")
    _ensure_media_playlist(current, "current")

    skipped_count = current.skip.skipped_segments if current.skip else 0
    if skipped_count < 0:
        raise PlaylistMergeError(
            "Cannot merge playlist: EXT-X-SKIP SKIPPED-SEGMENTS must be a "
            "non-negative integer, got %s" % skipped_count
        )

    previous_msns = _segment_sequence_numbers(previous, "previous")

    # EXT-X-MEDIA-SEQUENCE gives the Media Sequence Number of the first
    # Media Segment replaced by EXT-X-SKIP.  The parser numbers every
    # listed segment starting at that value, so the true sequence numbers
    # of the listed segments have to be shifted forward by
    # SKIPPED-SEGMENTS here.  The input playlists are never modified.
    first_skipped_msn = _first_msn(current)
    first_listed_msn = first_skipped_msn + skipped_count
    current_msns = [first_listed_msn + index for index in range(len(current.segments))]

    first_current_msn = current_msns[0] if current_msns else first_listed_msn
    last_current_msn = current_msns[-1] if current_msns else None
    last_previous_msn = previous_msns[-1] if previous_msns else None

    if last_current_msn is not None and last_previous_msn is not None:
        # A parts-only trailing segment is still being produced: a delta
        # update is allowed to replace it, so it does not count as the end
        # of the window when checking for a backwards sequence number.
        comparable_previous_msn = last_previous_msn
        last_previous_segment = previous.segments[-1]
        if last_previous_segment.uri is None and last_previous_segment.parts:
            comparable_previous_msn = last_previous_msn - 1

        # Only a partial overlap that ends before the previous window is a
        # genuine regression; a current playlist whose whole window lies
        # beyond the previous one is a non-overlapping reload and is handled
        # by the gap check below.
        if (
            first_skipped_msn < comparable_previous_msn
            and last_current_msn < comparable_previous_msn
        ):
            raise PlaylistMergeError(
                "Cannot merge playlist: Media Sequence Number moved "
                "backwards (last complete segment %s in the previous "
                "playlist but %s in the current one)"
                % (comparable_previous_msn, last_current_msn)
            )

    if skipped_count:
        if not previous_msns:
            raise PlaylistMergeError(
                "Cannot merge playlist delta update: the previous playlist "
                "has no segment to restore the %d skipped Media Segment(s) "
                "from" % skipped_count
            )
        missing = [
            msn
            for msn in range(first_skipped_msn, first_listed_msn)
            if msn not in previous_msns
        ]
        if missing:
            raise PlaylistMergeError(
                "Cannot merge playlist delta update: the previous playlist "
                "does not contain the skipped Media Segment(s) with Media "
                "Sequence Number(s) %s; a complete copy of the playlist is "
                "required" % ", ".join(str(msn) for msn in missing)
            )
    elif current_msns and previous_msns:
        # A regular reload cannot leave an unfilled gap in the window.
        gap = list(range(last_previous_msn + 1, first_current_msn))
        if gap:
            raise PlaylistMergeError(
                "Cannot merge playlist: the previous playlist does not "
                "contain the Media Segment(s) with Media Sequence "
                "Number(s) %s" % ", ".join(str(msn) for msn in gap)
            )

    removed_daterange_ids = _removed_daterange_ids(current)

    previous_by_msn = {segment.media_sequence: segment for segment in previous.segments}
    current_by_msn = {
        msn: segment for msn, segment in zip(current_msns, current.segments)
    }

    merged_by_msn = {}

    # Restore the segments replaced by EXT-X-SKIP and the segments that the
    # current playlist still lists but were already known.  Everything older
    # than the current playlist window is cropped out here.
    window_end = last_current_msn
    for msn, segment in previous_by_msn.items():
        if msn < first_skipped_msn:
            continue
        if window_end is not None and msn > window_end:
            continue
        if msn in current_by_msn:
            continue
        merged_by_msn[msn] = _copy_segment(
            segment, previous.base_uri, removed_daterange_ids
        )

    # Segments from the current playlist always win.  Copying them keeps the
    # inputs immutable and gives every segment a single base_uri owner.
    for msn, segment in current_by_msn.items():
        merged_by_msn[msn] = _copy_segment(
            segment, current.base_uri, removed_daterange_ids
        )

    merged_segments = SegmentList([merged_by_msn[msn] for msn in sorted(merged_by_msn)])
    _reassign_sequence_numbers(merged_segments)

    return _build_merged_playlist(previous, current, merged_segments, first_skipped_msn)


def _ensure_media_playlist(playlist, name):
    if not isinstance(playlist, M3U8):
        raise PlaylistMergeError(
            "Cannot merge playlist: the %s playlist must be an M3U8 object" % name
        )
    if playlist.is_variant:
        raise PlaylistMergeError(
            "Cannot merge playlist: the %s playlist is a Master (variant) "
            "playlist; only Media Playlists can be merged" % name
        )


def _segment_sequence_numbers(playlist, name):
    msns = []
    for segment in playlist.segments:
        if segment.media_sequence is None:
            raise PlaylistMergeError(
                "Cannot merge playlist: a segment of the %s playlist has no "
                "Media Sequence Number" % name
            )
        msns.append(segment.media_sequence)
    return msns


def _first_msn(playlist):
    return playlist.media_sequence or 0


def _removed_daterange_ids(playlist):
    if not playlist.skip or not playlist.skip.recently_removed_dateranges:
        return set()
    return {
        daterange_id
        for daterange_id in playlist.skip.recently_removed_dateranges.split("\t")
        if daterange_id
    }


def _copy_segment(segment, fallback_base_uri, removed_daterange_ids):
    copied = copy.deepcopy(segment)
    # deepcopy keeps every (possibly relative) URI and its base_uri, so
    # absolute_uri resolution keeps working for segments from both playlists.
    if copied.base_uri is None and fallback_base_uri is not None:
        copied.base_uri = fallback_base_uri

    if removed_daterange_ids and copied.dateranges:
        copied.dateranges = DateRangeList(
            [
                daterange
                for daterange in copied.dateranges
                if daterange.id not in removed_daterange_ids
            ]
        )

    if copied.parts is None:
        copied.parts = PartialSegmentList()
    if copied.dateranges is None:
        copied.dateranges = DateRangeList()
    return copied


def _reassign_sequence_numbers(segments):
    if not segments:
        return
    first_msn = segments[0].media_sequence
    for offset, segment in enumerate(segments):
        segment.media_sequence = first_msn + offset


def _build_merged_playlist(previous, current, merged_segments, first_msn):
    merged = M3U8()
    merged.data = {}

    merged.is_variant = False
    merged.is_endlist = current.is_endlist
    merged.is_i_frames_only = current.is_i_frames_only
    merged.is_independent_segments = current.is_independent_segments
    merged.is_images_only = current.is_images_only
    merged.target_duration = current.target_duration
    merged.version = current.version
    merged.allow_cache = current.allow_cache
    merged.playlist_type = current.playlist_type
    merged.server_control = current.server_control
    merged.part_inf = current.part_inf
    merged.start = current.start
    merged.preload_hint = current.preload_hint
    merged.rendition_reports = RenditionReportList(current.rendition_reports)
    merged.session_data = SessionDataList(current.session_data)
    merged.session_keys = list(current.session_keys)
    merged.content_steering = current.content_steering

    merged.discontinuity_sequence = _merge_discontinuity_sequence(previous, current)

    # The merged window is a materialized playlist: the EXT-X-SKIP tag and
    # the segments it replaced cannot coexist in the output.
    merged.skip = None

    merged.segments = merged_segments
    merged.media_sequence = first_msn if merged_segments else current.media_sequence

    first_program_date_time = next(
        (
            segment.program_date_time
            for segment in merged_segments
            if segment.program_date_time is not None
        ),
        next(
            (
                segment.current_program_date_time
                for segment in merged_segments
                if segment.current_program_date_time is not None
            ),
            current.program_date_time,
        ),
    )
    merged.program_date_time = first_program_date_time
    # Mirror the parser's representation: an explicit EXT-X-PROGRAM-DATE-TIME
    # is attached to the first segment of the window, so the value survives
    # a dumps()/loads() roundtrip even when the segment that originally
    # carried it was cropped out of the window.
    if merged_segments and first_program_date_time is not None:
        merged_segments[0].program_date_time = first_program_date_time

    merged.keys = _collect_keys(merged_segments)
    merged.segment_map = _collect_maps(merged_segments)
    merged.files = _build_files(merged.keys, merged_segments)

    merged.media = MediaList()
    merged.playlists = PlaylistList()
    merged.iframe_playlists = PlaylistList()
    merged.image_playlists = PlaylistList()

    merged._base_uri = current.base_uri
    return merged


def _merge_discontinuity_sequence(previous, current):
    previous_value = previous.discontinuity_sequence
    current_value = current.discontinuity_sequence
    if (
        previous_value is not None
        and current_value is not None
        and previous_value != current_value
    ):
        raise PlaylistMergeError(
            "Cannot merge playlists with incompatible "
            "EXT-X-DISCONTINUITY-SEQUENCE values (%s and %s)"
            % (previous_value, current_value)
        )
    return current_value if current_value is not None else previous_value


def _collect_keys(segments):
    # Segments are deep copies, so identity cannot be used: compare the
    # Key objects by value instead, preserving first-seen order.
    keys = []
    for segment in segments:
        key = segment.key
        if all(key != existing for existing in keys):
            keys.append(key)
    return keys


def _collect_maps(segments):
    maps = []
    for segment in segments:
        init_section = segment.init_section
        if init_section is not None and all(
            init_section != existing for existing in maps
        ):
            maps.append(init_section)
    return maps


def _build_files(keys, segments):
    files = []
    for key in keys:
        if key is not None and key.uri and key.uri not in files:
            files.append(key.uri)
    for segment in segments:
        if segment.uri and segment.uri not in files:
            files.append(segment.uri)
    return files
