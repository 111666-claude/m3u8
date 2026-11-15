# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.
"""
Optional content-steering (pathway selection) component.

Implements the client side of HLS Content Steering (RFC 8216bis):
given a master ``M3U8`` object that carries an
``#EXT-X-CONTENT-STEERING`` tag, fetch the Steering Manifest,
parse ``PATHWAY-PRIORITY`` / ``TTL`` / ``RELOAD-URI`` and expose the
currently preferred pathway together with its variant list.

The master playlist object is never modified, and pathway cloning
(``PATHWAY-CLONES``) is intentionally not implemented.
"""

import json
import time
from urllib.parse import urljoin, urlsplit

from m3u8.httpclient import DefaultHTTPClient

SUPPORTED_STEERING_VERSION = 1
DEFAULT_TTL = 300


class SteeringError(Exception):
    """Base class for content steering errors."""


class SteeringManifestError(SteeringError):
    """The steering manifest is invalid or unsupported."""


class SteeringRequestError(SteeringError):
    """The steering server could not be reached or returned an error."""


class SteeringManifest:
    """
    Immutable, parsed representation of a version 1 Steering Manifest.

    Attributes:

     `ttl`
       seconds until the manifest must be reloaded

     `reload_uri`
       absolute URI to use for the next reload (already resolved
       against the manifest URI), or None

     `pathway_priority`
       ordered list of pathway ids, most preferred first; every entry
       is guaranteed to exist in the master playlist
    """

    def __init__(self, ttl, reload_uri, pathway_priority):
        self.ttl = ttl
        self.reload_uri = reload_uri
        self.pathway_priority = pathway_priority

    def __repr__(self):
        return (
            f"SteeringManifest(ttl={self.ttl!r}, reload_uri={self.reload_uri!r}, "
            f"pathway_priority={self.pathway_priority!r})"
        )


def parse_steering_manifest(content, base_uri, known_pathways):
    """
    Parse and validate a Steering Manifest JSON document.

    `base_uri` is the URI the manifest was fetched from, used to
    resolve a relative RELOAD-URI. `known_pathways` is the collection
    of pathway ids actually present in the master playlist.

    Raises SteeringManifestError on invalid JSON, unsupported VERSION,
    illegal TTL, duplicate or unknown pathways, and on manifests that
    use PATHWAY-CLONES (pathway cloning is not implemented).
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as error:
        raise SteeringManifestError(f"invalid steering manifest JSON: {error}")

    if not isinstance(data, dict):
        raise SteeringManifestError("steering manifest must be a JSON object")

    version = data.get("VERSION")
    if version is None or isinstance(version, bool) or not isinstance(version, int):
        raise SteeringManifestError("steering manifest is missing a valid VERSION")
    if version != SUPPORTED_STEERING_VERSION:
        raise SteeringManifestError(f"unsupported steering manifest VERSION: {version}")

    if "PATHWAY-CLONES" in data:
        raise SteeringManifestError("PATHWAY-CLONES is not supported")

    ttl = data.get("TTL", DEFAULT_TTL)
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl < 0:
        raise SteeringManifestError(f"invalid steering manifest TTL: {ttl!r}")

    reload_uri = data.get("RELOAD-URI")
    if reload_uri is not None:
        if not isinstance(reload_uri, str):
            raise SteeringManifestError(
                f"invalid steering manifest RELOAD-URI: {reload_uri!r}"
            )
        reload_uri = urljoin(base_uri, reload_uri)

    priority = data.get("PATHWAY-PRIORITY")
    if (
        not isinstance(priority, list)
        or not priority
        or any(not isinstance(p, str) for p in priority)
    ):
        raise SteeringManifestError(
            "steering manifest PATHWAY-PRIORITY must be a non-empty list of strings"
        )

    seen = set()
    for pathway_id in priority:
        if pathway_id in seen:
            raise SteeringManifestError(
                f"duplicate pathway in PATHWAY-PRIORITY: {pathway_id!r}"
            )
        seen.add(pathway_id)
        if pathway_id not in known_pathways:
            raise SteeringManifestError(
                f"unknown pathway in PATHWAY-PRIORITY: {pathway_id!r}"
            )

    return SteeringManifest(ttl=ttl, reload_uri=reload_uri, pathway_priority=priority)


class PathwaySelector:
    """
    Selects the preferred CDN pathway for a master playlist using its
    Steering Manifest.

    Parameters:

     `master`
       an ``M3U8`` object whose ``content_steering`` attribute is set

     `http_client`
       object with a ``download(uri, timeout, headers)`` method,
       defaults to ``DefaultHTTPClient``

     `clock`
       zero-argument callable returning seconds, used for TTL caching;
       inject a fake clock in tests

     `timeout`
       per-request timeout passed to the http client

     `headers`
       extra request headers passed to the http client

    Network failures (timeouts, server errors) never raise from
    ``preferred_pathway``: the previously cached selection is kept,
    or, if none exists yet, the master playlist default pathway is
    used. Invalid manifests raise ``SteeringManifestError``.
    """

    def __init__(
        self, master, http_client=None, clock=None, timeout=None, headers=None
    ):
        if master.content_steering is None:
            raise ValueError("master playlist has no EXT-X-CONTENT-STEERING tag")
        self.master = master
        self.http_client = http_client or DefaultHTTPClient()
        self.clock = clock or time.monotonic
        self.timeout = timeout
        self.headers = headers or {}

        self._manifest_uri = self._resolve(master.content_steering.uri, master.base_uri)
        self._manifest = None
        self._expires_at = None
        self.last_error = None

    @staticmethod
    def _resolve(uri, base_uri):
        resolved = urljoin(base_uri or "", uri)
        if not urlsplit(resolved).scheme:
            raise SteeringError(
                f"cannot resolve steering SERVER-URI {uri!r} "
                f"against base_uri {base_uri!r}"
            )
        return resolved

    @property
    def available_pathways(self):
        """Pathway ids actually present in the master playlist, in order."""
        pathways = []
        for variant in list(self.master.playlists) + list(self.master.iframe_playlists):
            stream_info = getattr(variant, "stream_info", None) or getattr(
                variant, "iframe_stream_info", None
            )
            pathway_id = getattr(stream_info, "pathway_id", None)
            if pathway_id is not None and pathway_id not in pathways:
                pathways.append(pathway_id)
        return pathways

    @property
    def default_pathway(self):
        """
        Pathway to use without (or before) any steering information:
        the PATHWAY-ID of the EXT-X-CONTENT-STEERING tag, falling back
        to the pathway of the first variant in the master playlist.
        """
        if self.master.content_steering.pathway_id is not None:
            return self.master.content_steering.pathway_id
        pathways = self.available_pathways
        return pathways[0] if pathways else None

    @property
    def manifest(self):
        """The currently cached SteeringManifest, or None."""
        return self._manifest

    @property
    def expires_at(self):
        """Clock time at which the cached manifest becomes stale, or None."""
        return self._expires_at

    def refresh(self):
        """
        Fetch and parse the steering manifest now, updating the cache.
        Raises SteeringRequestError on network/server failure and
        SteeringManifestError on an invalid manifest.
        """
        try:
            content, _ = self.http_client.download(
                self._manifest_uri, self.timeout, self.headers
            )
        except Exception as error:
            raise SteeringRequestError(
                f"failed to download steering manifest from {self._manifest_uri}: {error}"
            ) from error

        manifest = parse_steering_manifest(
            content, self._manifest_uri, self.available_pathways
        )
        self._manifest = manifest
        self._expires_at = self.clock() + manifest.ttl
        if manifest.reload_uri:
            self._manifest_uri = manifest.reload_uri
        self.last_error = None
        return manifest

    @property
    def preferred_pathway(self):
        """
        The currently preferred pathway id. Reloads the manifest when
        the cache has expired; on request failure keeps the previous
        selection or falls back to the master playlist default.
        """
        if self._manifest is None or self.clock() >= self._expires_at:
            try:
                self.refresh()
            except SteeringRequestError as error:
                self.last_error = error
                # keep the stale-but-known selection if we have one
        if self._manifest is not None:
            return self._manifest.pathway_priority[0]
        return self.default_pathway

    def variants(self, pathway_id=None):
        """
        The master playlist variants belonging to `pathway_id`
        (defaults to the preferred pathway). Returns a new list; the
        master playlist is never modified.
        """
        if pathway_id is None:
            pathway_id = self.preferred_pathway
        if pathway_id is None:
            return list(self.master.playlists)
        return [
            playlist
            for playlist in self.master.playlists
            if playlist.stream_info.pathway_id == pathway_id
        ]
