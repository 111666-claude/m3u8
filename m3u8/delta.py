# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.
"""
Playlist Delta Update merging, as described in the Low-Latency HLS
specification (EXT-X-SKIP, section 6.3.7 of draft-pantos-hls-rfc8216bis).

A Playlist Delta Update only carries the tail of the Media Playlist: the
older Media Segments (and, optionally, EXT-X-DATERANGE tags) are replaced
by a single EXT-X-SKIP tag.  The client has to merge the delta update with
the previous, complete playlist it already holds in order to rebuild an
up-to-date complete playlist.
"""

from m3u8.model import M3U8, MergeConflictError, MergeError

__all__ = ["merge"]


def merge(previous, delta):
    """
    Merge a Playlist Delta Update (a parsed ``M3U8`` containing an
    ``EXT-X-SKIP`` tag) with the previous version of the same Media
    Playlist.

    The skipped Media Segments are restored from ``previous`` by Media
    Sequence Number, the overlapping segments are replaced by the ones
    carried by ``delta`` and the brand new segments are appended.
    EXT-X-KEY, EXT-X-MAP, EXT-X-DATERANGE and EXT-X-PART associations are
    preserved.

    Neither ``previous`` nor ``delta`` are modified: a brand new ``M3U8``
    object is returned.

    A :class:`~m3u8.model.MergeError` (or its :class:`MergeConflictError`
    subclass) is raised when the delta cannot be merged with the
    information available in ``previous``.
    """
    if not isinstance(previous, M3U8) or not isinstance(delta, M3U8):
        raise MergeError("both arguments must be M3U8 instances")

    if previous.is_variant or delta.is_variant:
        raise MergeError("playlist delta updates only apply to Media Playlists")

    previous_data = previous.data
    delta_data = delta.data

    # A playlist without an EXT-X-SKIP tag is a complete playlist, there
    # is nothing to merge: simply return an independent copy of it.
    if not delta.skip:
        return _rebuild(_deep_copy(delta_data), delta)

    skip = delta_data.get("skip") or {}
    skipped = skip.get("skipped_segments")
    if not isinstance(skipped, int) or skipped <= 0:
        raise MergeError(
            "EXT-X-SKIP SKIPPED-SEGMENTS must be a positive integer, got %r"
            % (skipped,)
        )

    previous_segments = previous_data.get("segments", [])
    delta_segments = delta_data.get("segments", [])
    if not previous_segments:
        raise MergeError(
            "the previous playlist does not contain any segment, "
            "the skipped information is not available"
        )
    if not delta_segments:
        raise MergeError("a playlist delta update must list at least one Media Segment")

    previous_ms = previous_data.get("media_sequence") or 0
    delta_ms = delta_data.get("media_sequence") or 0
    previous_first_msn = previous_ms
    delta_first_msn = delta_ms + skipped

    _check_duplicate_segment_uris(previous_segments, previous_first_msn)
    previous_by_msn = _index_segments(previous_segments, previous_first_msn)
    # The delta lists its explicit segments starting at the skip
    # boundary, so their Media Sequence Numbers begin at delta_first_msn.
    _check_duplicate_segment_uris(delta_segments, delta_first_msn)
    delta_by_msn = _index_segments(delta_segments, delta_first_msn)
    previous_last_msn = previous_first_msn + len(previous_segments) - 1
    delta_last_msn = delta_first_msn + len(delta_segments) - 1

    # The delta must not move the window backwards.
    if delta_first_msn < previous_first_msn:
        raise MergeError(
            "sequence number regression: the delta starts at Media Sequence "
            "Number %d, the previous playlist already starts at %d"
            % (delta_first_msn, previous_first_msn)
        )

    # Every skipped segment has to be available in the previous playlist.
    if delta_first_msn > previous_last_msn + 1:
        raise MergeError(
            "the previous playlist window is too short: it ends at Media "
            "Sequence Number %d but the delta needs the skipped segments "
            "up to %d" % (previous_last_msn, delta_first_msn - 1)
        )

    if (previous_data.get("discontinuity_sequence") or 0) != (
        delta_data.get("discontinuity_sequence") or 0
    ):
        raise MergeConflictError(
            "EXT-X-DISCONTINUITY-SEQUENCE mismatch between the previous "
            "playlist (%r) and the delta update (%r)"
            % (
                previous_data.get("discontinuity_sequence"),
                delta_data.get("discontinuity_sequence"),
            )
        )

    merged_segments = []

    # 1. Restore the segments the delta replaced with the EXT-X-SKIP tag.
    for msn in range(previous_first_msn, delta_first_msn):
        if msn not in previous_by_msn:
            raise MergeError(
                "missing Media Segment with Media Sequence Number %d in the "
                "previous playlist" % msn
            )
        merged_segments.append(_deep_copy(previous_by_msn[msn]))

    # 2. Replace the segments present in both playlists with the delta
    #    versions, checking that the effective KEY and MAP still agree.
    #    Tags omitted by the delta are inherited from the previous segment
    #    (normal EXT-X-KEY / EXT-X-MAP inheritance semantics).
    overlap_end = min(delta_last_msn, previous_last_msn)
    previous_effective_key = _last_tag_before(previous_by_msn, delta_first_msn, "key")
    previous_effective_map = _last_tag_before(
        previous_by_msn, delta_first_msn, "init_section"
    )
    # Tags omitted by the delta keep the value they had at the skip
    # boundary, i.e. the delta starts from the inherited KEY/MAP state.
    delta_effective_key = previous_effective_key
    delta_effective_map = previous_effective_map
    for msn in range(delta_first_msn, overlap_end + 1):
        old_segment = previous_by_msn[msn]
        new_segment = delta_by_msn[msn]

        if "key" in old_segment:
            previous_effective_key = old_segment["key"]
        if "init_section" in old_segment:
            previous_effective_map = old_segment["init_section"]
        if "key" in new_segment:
            delta_effective_key = new_segment["key"]
        if "init_section" in new_segment:
            delta_effective_map = new_segment["init_section"]

        _check_overlapping_segment(
            previous_effective_key,
            delta_effective_key,
            previous_effective_map,
            delta_effective_map,
            msn,
        )
        replacement = _materialize_inherited_tags(
            _deep_copy(new_segment), delta_effective_key, delta_effective_map
        )
        _inherit_dateranges(old_segment, replacement)
        merged_segments.append(replacement)

    # 3. Append the brand new segments only carried by the delta update.
    for msn in range(overlap_end + 1, delta_last_msn + 1):
        if msn not in delta_by_msn:
            raise MergeError(
                "missing Media Segment with Media Sequence Number %d in the "
                "delta playlist" % msn
            )
        new_segment = _deep_copy(delta_by_msn[msn])
        if "key" in new_segment:
            delta_effective_key = new_segment["key"]
        if "init_section" in new_segment:
            delta_effective_map = new_segment["init_section"]
        merged_segments.append(
            _materialize_inherited_tags(
                new_segment, delta_effective_key, delta_effective_map
            )
        )

    merged_data = _deep_copy(delta_data)
    merged_data["segments"] = merged_segments
    # The rebuilt playlist is a complete playlist: the EXT-X-SKIP tag is
    # gone and MEDIA-SEQUENCE points at the restored first segment.
    merged_data["skip"] = {}
    merged_data["media_sequence"] = previous_first_msn
    _rebuild_global_tags(merged_data)

    _consolidate_dateranges(merged_data, skip)

    return _rebuild(merged_data, delta)


def _materialize_inherited_tags(segment, effective_key, effective_map):
    """
    Explicitly write the currently effective EXT-X-KEY / EXT-X-MAP onto a
    segment coming from the delta update, so the rebuilt complete playlist
    keeps the tags even though the delta omitted them.  Tags explicitly
    restated by the delta (including an explicit ``METHOD=NONE``) take
    precedence over the inherited value.
    """
    if "key" not in segment:
        segment["key"] = effective_key
    if "init_section" not in segment:
        segment["init_section"] = effective_map
    return segment


def _inherit_dateranges(old_segment, new_segment):
    """
    Preserve EXT-X-DATERANGE tags the delta update did not restate on an
    overlapping segment (or on its partial segments).  The delta version
    wins when it carries a range with the same ID.
    """
    new_range_ids = {
        daterange["id"] for daterange in new_segment.get("dateranges") or []
    }
    for daterange in old_segment.get("dateranges") or []:
        if daterange["id"] not in new_range_ids:
            new_segment.setdefault("dateranges", []).append(_deep_copy(daterange))

    old_parts = old_segment.get("parts") or []
    new_parts = new_segment.get("parts") or []
    for old_part, new_part in zip(old_parts, new_parts):
        new_part_ids = {
            daterange["id"] for daterange in new_part.get("dateranges") or []
        }
        for daterange in old_part.get("dateranges") or []:
            if daterange["id"] not in new_part_ids:
                new_part.setdefault("dateranges", []).append(_deep_copy(daterange))


def _rebuild_global_tags(merged_data):
    """
    Rebuild the playlist level ``keys`` and ``segment_map`` lists from the
    merged segments: restored segments may carry KEY/MAP dictionaries that
    only existed in the previous playlist, so the delta's global lists are
    not sufficient on their own.  Order of first appearance is preserved.
    """
    keys = []
    segment_maps = []
    for segment in merged_data["segments"]:
        key = segment.get("key")
        if key is None:
            if None not in keys:
                keys.append(None)
        elif key not in keys:
            keys.append(key)

        init_section = segment.get("init_section")
        if init_section is not None and init_section not in segment_maps:
            segment_maps.append(init_section)

    # Keep every KEY/MAP declared at the playlist level even if no merged
    # segment references it anymore (for instance a KEY tag carried by the
    # delta before its first explicit segment).
    for key in merged_data.get("keys", []):
        if key not in keys:
            keys.append(key)
    for init_section in merged_data.get("segment_map", []):
        if init_section not in segment_maps:
            segment_maps.append(init_section)

    merged_data["keys"] = keys
    merged_data["segment_map"] = segment_maps


def _rebuild(data, source):
    merged = M3U8.__new__(M3U8)
    merged.data = data
    merged._base_uri = source.base_uri
    merged._initialize_attributes()
    merged.base_path = source.base_path
    return merged


def _index_segments(segments, base_msn):
    """
    Map every segment to its Media Sequence Number.
    """
    return {base_msn + index: segment for index, segment in enumerate(segments)}


def _check_duplicate_segment_uris(segments, base_msn):
    """
    Reject a playlist carrying the same segment URI more than once, which
    would make two Media Sequence Numbers resolve to the same Media
    Segment.  Only reachable through hand-crafted objects, the parser can
    never produce such input.
    """
    seen_uris = set()
    for index, segment in enumerate(segments):
        uri = segment.get("uri")
        if uri is not None and uri in seen_uris:
            raise MergeError(
                "duplicate Media Segment %r at Media Sequence Number %d"
                % (uri, base_msn + index)
            )
        seen_uris.add(uri)


def _last_tag_before(indexed_segments, msn, attribute):
    """
    Return the last explicit value of ``attribute`` (``"key"`` or
    ``"init_section"``) among the segments with a Media Sequence Number
    strictly below ``msn``, or ``None`` if the tag was never set.
    """
    effective = None
    for candidate_msn in sorted(indexed_segments):
        if candidate_msn >= msn:
            break
        segment = indexed_segments[candidate_msn]
        if attribute in segment:
            effective = segment[attribute]
    return effective


def _check_overlapping_segment(old_key, new_key, old_map, new_map, msn):
    if not _same_tag(
        old_key, new_key, ("method", "uri", "iv", "keyformat", "keyformatversions")
    ):
        raise MergeConflictError(
            "EXT-X-KEY conflict on the overlapping Media Segment with Media "
            "Sequence Number %d: %r != %r" % (msn, old_key, new_key)
        )

    if not _same_tag(old_map, new_map, ("uri", "byterange")):
        raise MergeConflictError(
            "EXT-X-MAP conflict on the overlapping Media Segment with Media "
            "Sequence Number %d: %r != %r" % (msn, old_map, new_map)
        )


def _same_tag(left, right, attributes):
    if left is None or right is None:
        return left is None and right is None
    return all(left.get(attribute) == right.get(attribute) for attribute in attributes)


def _consolidate_dateranges(merged_data, skip):
    """
    Consolidate every EXT-X-DATERANGE reachable from the merged segments.

    The IDs listed in RECENTLY-REMOVED-DATERANGES are dropped, ranges with
    the same ID are merged (common attributes must agree) and every
    attachment point is rewritten to reference the consolidated copy.

    Only the already deep-copied merged data is walked, so the input
    playlists are never touched.
    """
    removed = set()
    removed_raw = skip.get("recently_removed_dateranges")
    if removed_raw:
        removed = {
            identifier.strip()
            for identifier in removed_raw.split("\t")
            if identifier.strip()
        }

    consolidated = {}

    def ingest(daterange):
        range_id = daterange["id"]
        existing = consolidated.get(range_id)
        if existing is None:
            consolidated[range_id] = daterange
            return
        for attribute, value in daterange.items():
            if value is None:
                continue
            if existing.get(attribute) is None:
                existing[attribute] = value
            elif existing[attribute] != value:
                raise MergeConflictError(
                    "conflicting EXT-X-DATERANGE attributes for ID %r: %s is "
                    "both %r and %r" % (range_id, attribute, existing[attribute], value)
                )

    def walk(segments):
        for segment in segments:
            for daterange in segment.get("dateranges") or []:
                ingest(daterange)
            for part in segment.get("parts") or []:
                for daterange in part.get("dateranges") or []:
                    ingest(daterange)

    # Every daterange reachable from the restored, replaced or appended
    # segments is already part of the deep-copied merged data.
    walk(merged_data.get("segments", []))

    def reattach(dateranges, emitted):
        if not dateranges:
            return dateranges
        result = []
        for daterange in dateranges:
            range_id = daterange["id"]
            if range_id in removed or range_id in emitted:
                continue
            emitted.add(range_id)
            result.append(consolidated[range_id])
        return result

    emitted = set()
    for segment in merged_data.get("segments", []):
        if segment.get("dateranges"):
            segment["dateranges"] = reattach(segment["dateranges"], emitted)
        for part in segment.get("parts") or []:
            if part.get("dateranges"):
                part["dateranges"] = reattach(part["dateranges"], emitted)


def _deep_copy(value):
    if isinstance(value, dict):
        return {key: _deep_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_deep_copy(item) for item in value)
    if isinstance(value, set):
        return {_deep_copy(item) for item in value}
    # datetime and every immutable scalar are safe to share.
    return value
