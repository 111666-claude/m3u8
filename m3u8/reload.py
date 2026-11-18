# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

"""
Planning of blocking playlist reload requests for Low-Latency HLS.

When a media playlist advertises ``CAN-BLOCK-RELOAD=YES`` through its
``#EXT-X-SERVER-CONTROL`` tag, a client does not need to poll the playlist on
a timer. It can instead ask the server to hold the request open until a new
Media Segment or Partial Segment becomes available, by appending the
``_HLS_msn`` and ``_HLS_part`` query parameters defined in the HLS
specification.

The :class:`~m3u8.model.M3U8` object is stateless regarding the reload
conversation, so :meth:`~m3u8.model.M3U8.plan_blocking_reload` only needs the
playlist itself plus the URI it was fetched from.
"""

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Media Sequence Numbers wrap around once they exhaust the unsigned 64 bit
# range reserved by the HLS specification.
_MSN_MODULUS = 2**64

# Query parameters reserved by the HLS specification for reload requests.
# They are rebuilt from the current playlist on every plan rather than carried
# over from a previously planned URI.
_HLS_RESERVED_PARAMS = frozenset(("_HLS_msn", "_HLS_part", "_HLS_skip"))

# Reasons that make a blocking reload impossible; the caller must fall back to
# a plain, non-blocking reload in every one of these cases.
REASON_VARIANT_PLAYLIST = "variant-playlist"
REASON_ENDLIST = "endlist"
REASON_BLOCKING_NOT_ALLOWED = "blocking-reload-not-allowed"
REASON_EMPTY_PLAYLIST = "empty-playlist"
REASON_NO_NEXT_PART = "no-next-part"


@dataclass(frozen=True)
class BlockingReloadRequest:
    """
    The request a client should issue for its next playlist reload.

    `blocking`
      ``True`` when the server may hold the response until new media is
      available; ``False`` when the request is a regular, non-blocking reload.

    `uri`
      The full URI to request, including the original query string and, for a
      blocking request, the ``_HLS_msn`` / ``_HLS_part`` / ``_HLS_skip``
      parameters.

    `msn`
      The value sent as ``_HLS_msn`` for a blocking request, otherwise
      ``None``.

    `part`
      The value sent as ``_HLS_part`` for a blocking request, otherwise
      ``None``. It is omitted entirely when the next requested item is a
      whole Media Segment.

    `skip`
      The value sent as ``_HLS_skip`` (``"YES"`` or ``"v2"``) when the server
      advertises ``CAN-SKIP-UNTIL`` and this is not the first blocking reload,
      otherwise ``None``.

    `reason`
      ``None`` for a blocking request; otherwise one of the ``REASON_*``
      constants explaining why a regular reload is required instead.
    """

    blocking: bool
    uri: str
    msn: int = None
    part: int = None
    skip: str = None
    reason: str = None

    @property
    def is_blocking(self):
        """Whether the request uses the blocking reload mechanism."""
        return self.blocking


def _append_query(uri, extra_params):
    """Return ``uri`` with ``extra_params`` appended to its query string.

    Parameters already present in the URI are preserved and keep their
    original position; the new parameters are simply added after them.
    Reserved ``_HLS_*`` reload parameters found in the URI are stripped first
    so repeated planning never stacks stale values.
    """
    scheme, netloc, path, query, fragment = urlsplit(uri)
    params = [
        (name, value)
        for name, value in parse_qsl(query, keep_blank_values=True)
        if name not in _HLS_RESERVED_PARAMS
    ]
    params.extend(extra_params)
    new_query = urlencode(params)
    return urlunsplit((scheme, netloc, path, new_query, fragment))


def _plain_reload(uri, reason):
    """Build a non-blocking reload request pointing at the plain URI."""
    return BlockingReloadRequest(blocking=False, uri=uri, reason=reason)


def plan_blocking_reload(playlist, uri, *, first_reload=False, skip=None):
    """
    Plan the next reload request for a Low-Latency media playlist.

    `playlist`
      The current :class:`~m3u8.model.M3U8` media playlist.

    `uri`
      The URI the playlist was loaded from. Its query string is preserved;
      only the HLS-reserved reload parameters (``_HLS_msn``, ``_HLS_part`` and
      ``_HLS_skip``) are rebuilt from the current playlist.

    `first_reload`
      Must be ``True`` for the very first blocking reload of a playback
      session. The HLS specification forbids ``_HLS_skip`` on that request,
      so a requested ``skip`` value is ignored while this is ``True``.

    `skip`
      Optional ``_HLS_skip`` value, either ``"YES"`` or ``"v2"``. It is only
      forwarded when the playlist's ``#EXT-X-SERVER-CONTROL`` tag advertises
      ``CAN-SKIP-UNTIL`` (and, for ``v2``, ``CAN-SKIP-DATERANGES=YES``).

    Returns a :class:`BlockingReloadRequest`.
    """
    if playlist.is_variant:
        return _plain_reload(uri, REASON_VARIANT_PLAYLIST)

    # A finished playlist never gets new media, blocking would only waste a
    # held-open connection.
    if playlist.is_endlist:
        return _plain_reload(uri, REASON_ENDLIST)

    # An empty window carries no Media Sequence Number to anchor a blocking
    # request on; reload it plainly until media shows up.
    if not playlist.segments:
        return _plain_reload(uri, REASON_EMPTY_PLAYLIST)

    server_control = playlist.server_control
    can_block = bool(
        server_control
        and isinstance(server_control.can_block_reload, str)
        and server_control.can_block_reload.upper() == "YES"
    )
    if not can_block:
        return _plain_reload(uri, REASON_BLOCKING_NOT_ALLOWED)

    target = _next_requested_item(playlist)
    if target is None:
        return _plain_reload(uri, REASON_EMPTY_PLAYLIST)

    msn, part = target
    if part is None:
        return _plain_reload(uri, REASON_NO_NEXT_PART)

    extra = [("_HLS_msn", str(msn)), ("_HLS_part", str(part))]

    effective_skip = _effective_skip(
        server_control, first_reload=first_reload, skip=skip
    )
    if effective_skip is not None:
        extra.append(("_HLS_skip", effective_skip))

    return BlockingReloadRequest(
        blocking=True,
        uri=_append_query(uri, extra),
        msn=msn,
        part=part,
        skip=effective_skip,
    )


def _effective_skip(server_control, *, first_reload, skip):
    """Validate the requested _HLS_skip against the server capabilities."""
    if first_reload or not skip:
        return None

    # _HLS_skip is only defined when the server is willing to deliver
    # playlist delta updates.
    if server_control.can_skip_until is None:
        return None

    if skip == "v2":
        # v2 additionally requires permission to skip Date Ranges; otherwise
        # fall back to the baseline directive rather than sending an
        # unsupported value.
        can_skip_dateranges = server_control.can_skip_dateranges
        if not (
            isinstance(can_skip_dateranges, str)
            and can_skip_dateranges.upper() == "YES"
        ):
            return "YES"
        return "v2"

    # Any unrecognized value is normalized to the baseline directive.
    return "YES"


def _last_media_sequence(playlist):
    """
    Return the Media Sequence Number of the last segment in the window.

    Segments removed through an ``#EXT-X-SKIP`` tag are not present in
    ``playlist.segments`` and the parser does not advance
    ``media_sequence`` for them, so the skipped count has to be added back.
    """
    if not playlist.segments:
        return None

    last_msn = playlist.segments[-1].media_sequence
    skip = playlist.skip
    if skip is not None:
        skipped = (
            skip.skipped_segments
            if not isinstance(skip, dict)
            else skip.get("skipped_segments", 0)
        )
        last_msn += skipped
    return last_msn


def _next_requested_item(playlist):
    """
    Compute the ``(msn, part)`` pair the client is waiting for next.

    Returns ``None`` when the playlist carries no media at all. ``part`` is
    ``None`` when the next requested item is a whole Media Segment rather
    than a Partial Segment.
    """
    last_msn = _last_media_sequence(playlist)
    if last_msn is None:
        return None

    last_segment = playlist.segments[-1]

    # A PART preload hint announces the Partial Segment that will be appended
    # to the last (possibly still incomplete) segment.
    preload_is_part = bool(
        playlist.preload_hint
        and isinstance(playlist.preload_hint.hint_type, str)
        and playlist.preload_hint.hint_type.upper() == "PART"
    )
    if preload_is_part:
        return last_msn % _MSN_MODULUS, len(last_segment.parts)

    # An incomplete last segment (PART tags with no following Media Segment
    # URI) is filled by more Partial Segments.
    if last_segment.uri is None and last_segment.parts:
        return last_msn % _MSN_MODULUS, len(last_segment.parts)

    # The playlist ends in complete Media Segments (or a MAP preload hint):
    # wait for the first Partial Segment of the next segment.
    return (last_msn + 1) % _MSN_MODULUS, 0
