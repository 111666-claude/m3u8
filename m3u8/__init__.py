# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import os
from urllib.parse import urljoin, urlsplit

from m3u8.httpclient import DefaultHTTPClient
from m3u8.model import (
    M3U8,
    ContentSteering,
    DateRange,
    DateRangeList,
    IFramePlaylist,
    ImagePlaylist,
    Key,
    Media,
    MediaList,
    PartialSegment,
    PartialSegmentList,
    PartInformation,
    Playlist,
    PlaylistList,
    PreloadHint,
    RenditionReport,
    RenditionReportList,
    Segment,
    SegmentList,
    ServerControl,
    Skip,
    Start,
    Tiles,
    VariableDefinition,
)
from m3u8.parser import ParseError, parse
from m3u8.variables import (
    CyclicVariableReferenceError,
    DuplicateVariableError,
    IllegalSubstitutionValueError,
    InvalidVariableNameError,
    UndefinedVariableError,
    VariableDefinitionError,
    VariableSubstitutionError,
    query_variables_from_uri,
)

__all__ = (
    "M3U8",
    "ContentSteering",
    "CyclicVariableReferenceError",
    "DateRange",
    "DateRangeList",
    "DuplicateVariableError",
    "IFramePlaylist",
    "IllegalSubstitutionValueError",
    "ImagePlaylist",
    "InvalidVariableNameError",
    "Key",
    "Media",
    "MediaList",
    "ParseError",
    "PartInformation",
    "PartialSegment",
    "PartialSegmentList",
    "Playlist",
    "PlaylistList",
    "PreloadHint",
    "RenditionReport",
    "RenditionReportList",
    "Segment",
    "SegmentList",
    "ServerControl",
    "Skip",
    "Start",
    "Tiles",
    "UndefinedVariableError",
    "VariableDefinition",
    "VariableDefinitionError",
    "VariableSubstitutionError",
    "load",
    "loads",
    "parse",
    "query_variables_from_uri",
)


def loads(
    content,
    uri=None,
    custom_tags_parser=None,
    variables=None,
    query_variables=None,
):
    """
    Given a string with a m3u8 content, returns a M3U8 object.
    Optionally parses a uri to set a correct base_uri on the M3U8 object.

    `variables` are values inherited from the Multivariant Playlist (or
    provided explicitly); EXT-X-DEFINE IMPORT declarations are resolved
    against them. When `uri` carries a query string its percent-decoded
    parameters satisfy QUERYPARAM declarations, unless `query_variables`
    is given explicitly.
    Raises ValueError if invalid content
    """
    if query_variables is None and uri is not None:
        query_variables = query_variables_from_uri(uri)

    if uri is None:
        return M3U8(
            content,
            custom_tags_parser=custom_tags_parser,
            variables=variables,
            query_variables=query_variables,
        )
    base_uri = urljoin(uri, ".")
    return M3U8(
        content,
        base_uri=base_uri,
        custom_tags_parser=custom_tags_parser,
        variables=variables,
        query_variables=query_variables,
    )


def load(
    uri,
    timeout=None,
    headers={},
    custom_tags_parser=None,
    http_client=DefaultHTTPClient(),
    verify_ssl=True,
    variables=None,
    query_variables=None,
):
    """
    Retrieves the content from a given URI and returns a M3U8 object.
    Raises ValueError if invalid content or IOError if request fails.

    `variables` are values inherited from the Multivariant Playlist;
    QUERYPARAM declarations are satisfied by the query string of the
    final URI (including redirects), unless `query_variables` is given.
    """
    base_uri_parts = urlsplit(uri)
    if base_uri_parts.scheme and base_uri_parts.netloc:
        downloaded = http_client.download(uri, timeout, headers, verify_ssl)
        if len(downloaded) == 3:
            content, base_uri, uri_query_variables = downloaded
        else:
            # Backwards compatibility for custom HTTP clients still
            # returning a (content, base_uri) pair.
            content, base_uri = downloaded
            uri_query_variables = query_variables_from_uri(uri)
        if query_variables is None:
            query_variables = uri_query_variables
        return M3U8(
            content,
            base_uri=base_uri,
            custom_tags_parser=custom_tags_parser,
            variables=variables,
            query_variables=query_variables,
        )
    else:
        return _load_from_file(
            uri,
            custom_tags_parser,
            variables=variables,
            query_variables=query_variables,
        )


def _load_from_file(uri, custom_tags_parser=None, variables=None, query_variables=None):
    with open(uri, encoding="utf8") as fileobj:
        raw_content = fileobj.read().strip()
    base_uri = os.path.dirname(uri)
    if query_variables is None:
        query_variables = query_variables_from_uri(uri)
    return M3U8(
        raw_content,
        base_uri=base_uri,
        custom_tags_parser=custom_tags_parser,
        variables=variables,
        query_variables=query_variables,
    )
