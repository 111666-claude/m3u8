![image](https://github.com/globocom/m3u8/actions/workflows/main.yml/badge.svg) [![image](https://badge.fury.io/py/m3u8.svg)](https://badge.fury.io/py/m3u8)

# m3u8

Python [m3u8](https://tools.ietf.org/html/rfc8216) parser.

# Documentation

## Loading a playlist

To load a playlist into an object from uri, file path or directly from
string, use the `load/loads` functions:

```python
import m3u8

playlist = m3u8.load('http://videoserver.com/playlist.m3u8')  # this could also be an absolute filename
print(playlist.segments)
print(playlist.target_duration)

# if you already have the content as string, use

playlist = m3u8.loads('#EXTM3U8 ... etc ... ')
```

## Dumping a playlist

To dump a playlist from an object to the console or a file, use the
`dump/dumps` functions:

``` python
import m3u8

playlist = m3u8.load('http://videoserver.com/playlist.m3u8')
print(playlist.dumps())

# if you want to write a file from its content

playlist.dump('playlist.m3u8')
```

## Generating cross-rendition reports

Low-Latency HLS publishes video, audio and subtitle renditions as separate
Media Playlists that scroll independently. Each playlist has to carry one
`EXT-X-RENDITION-REPORT` tag for every *other* rendition, telling players how
far that rendition has progressed. Use `generate_rendition_reports` to build
those tags from the current snapshot of all tracks:

``` python
import m3u8

tracks = {
    'video/index.m3u8': m3u8.load('http://videoserver.com/hls/video/index.m3u8'),
    'audio/index.m3u8': m3u8.load('http://videoserver.com/hls/audio/index.m3u8'),
    'subs/index.m3u8': m3u8.load('http://videoserver.com/hls/subs/index.m3u8'),
}

m3u8.generate_rendition_reports(tracks)

for playlist in tracks.values():
    print(playlist.dumps())
```

For every playlist one report is generated per other track, in input order.
`URI` is relative to the URI of the playlist that contains the report
(`../audio/index.m3u8`), `LAST-MSN` comes from `EXT-X-MEDIA-SEQUENCE`, the
number of listed segments and `SKIPPED-SEGMENTS`, and `LAST-MSN`/`LAST-PART`
point at the last Partial Segment when the referenced rendition contains
`EXT-X-PART` tags. Existing reports are replaced instead of appended to, so
the same function can be called again after the windows scroll; segments,
keys, `EXT-X-MAP`, Partial Segments and all other tags are left untouched,
and the resulting playlists survive `dumps`/`loads` round trips.

Tracks may be given as a `{uri: playlist}` mapping, as `(uri, playlist)`
pairs, as `m3u8.RenditionTrack` objects, or as playlists that carry a `uri`
attribute; playlists can also be passed as playlist content strings. All
tracks are validated before any playlist is modified: a
`m3u8.RenditionReportError` (a `ValueError`) is raised for duplicate URIs,
empty or expired playlists, master playlists, tracks without a determinable
Media Sequence Number and illegal `EXT-X-PART` layouts, so an invalid
snapshot is never published.

# Supported tags

-   [\#EXT-X-TARGETDURATION](https://tools.ietf.org/html/rfc8216#section-4.3.3.1)
-   [\#EXT-X-MEDIA-SEQUENCE](https://tools.ietf.org/html/rfc8216#section-4.3.3.2)
-   [\#EXT-X-DISCONTINUITY-SEQUENCE](https://tools.ietf.org/html/rfc8216#section-4.3.3.3)
-   [\#EXT-X-PROGRAM-DATE-TIME](https://tools.ietf.org/html/rfc8216#section-4.3.2.6)
-   [\#EXT-X-MEDIA](https://tools.ietf.org/html/rfc8216#section-4.3.4.1)
-   [\#EXT-X-PLAYLIST-TYPE](https://tools.ietf.org/html/rfc8216#section-4.3.3.5)
-   [\#EXT-X-KEY](https://tools.ietf.org/html/rfc8216#section-4.3.2.4)
-   [\#EXT-X-STREAM-INF](https://tools.ietf.org/html/rfc8216#section-4.3.4.2)
-   [\#EXT-X-VERSION](https://tools.ietf.org/html/rfc8216#section-4.3.1.2)
-   [\#EXT-X-ALLOW-CACHE](https://datatracker.ietf.org/doc/html/draft-pantos-http-live-streaming-07#section-3.3.6)
-   [\#EXT-X-ENDLIST](https://tools.ietf.org/html/rfc8216#section-4.3.3.4)
-   [\#EXTINF](https://tools.ietf.org/html/rfc8216#section-4.3.2.1)
-   [\#EXT-X-I-FRAMES-ONLY](https://tools.ietf.org/html/rfc8216#section-4.3.3.6)
-   [\#EXT-X-BITRATE](https://datatracker.ietf.org/doc/html/draft-pantos-hls-rfc8216bis#section-4.4.4.8)
-   [\#EXT-X-BYTERANGE](https://tools.ietf.org/html/rfc8216#section-4.3.2.2)
-   [\#EXT-X-I-FRAME-STREAM-INF](https://tools.ietf.org/html/rfc8216#section-4.3.4.3)
-   [\#EXT-X-IMAGES-ONLY](https://github.com/image-media-playlist/spec/blob/master/image_media_playlist_v0_4.pdf)
-   [\#EXT-X-IMAGE-STREAM-INF](https://github.com/image-media-playlist/spec/blob/master/image_media_playlist_v0_4.pdf)
-   [\#EXT-X-TILES](https://github.com/image-media-playlist/spec/blob/master/image_media_playlist_v0_4.pdf)
-   [\#EXT-X-DISCONTINUITY](https://tools.ietf.org/html/rfc8216#section-4.3.2.3)
-   \#EXT-X-CUE-OUT
-   \#EXT-X-CUE-OUT-CONT
-   \#EXT-X-CUE-IN
-   \#EXT-X-CUE-SPAN
-   \#EXT-OATCLS-SCTE35
-   [\#EXT-X-INDEPENDENT-SEGMENTS](https://tools.ietf.org/html/rfc8216#section-4.3.5.1)
-   [\#EXT-X-MAP](https://tools.ietf.org/html/rfc8216#section-4.3.2.5)
-   [\#EXT-X-START](https://tools.ietf.org/html/rfc8216#section-4.3.5.2)
-   [\#EXT-X-SERVER-CONTROL](https://datatracker.ietf.org/doc/html/draft-pantos-hls-rfc8216bis#section-4.4.3.8)
-   [\#EXT-X-PART-INF](https://datatracker.ietf.org/doc/html/draft-pantos-hls-rfc8216bis#section-4.4.3.7)
-   [\#EXT-X-PART](https://datatracker.ietf.org/doc/html/draft-pantos-hls-rfc8216bis#section-4.4.4.9)
-   [\#EXT-X-RENDITION-REPORT](https://datatracker.ietf.org/doc/html/draft-pantos-hls-rfc8216bis#section-4.4.5.4)
-   [\#EXT-X-SKIP](https://datatracker.ietf.org/doc/html/draft-pantos-hls-rfc8216bis#section-4.4.5.2)
-   [\#EXT-X-SESSION-DATA](https://tools.ietf.org/html/rfc8216#section-4.3.4.4)
-   [\#EXT-X-PRELOAD-HINT](https://datatracker.ietf.org/doc/html/draft-pantos-hls-rfc8216bis-09#section-4.4.5.3)
-   [\#EXT-X-SESSION-KEY](https://tools.ietf.org/html/rfc8216#section-4.3.4.5)
-   [\#EXT-X-DATERANGE](https://tools.ietf.org/html/rfc8216#section-4.3.2.7)
-   [\#EXT-X-GAP](https://tools.ietf.org/html/draft-pantos-hls-rfc8216bis-05#section-4.4.2.7)
-   [\#EXT-X-CONTENT-STEERING](https://tools.ietf.org/html/draft-pantos-hls-rfc8216bis-10#section-4.4.6.64)

# Frequently Asked Questions

-   [FAQ](https://github.com/globocom/m3u8/wiki/FAQ)

# Running Tests

``` bash
$ ./runtests
```

# Contributing

All contributions are welcome, but we will merge a pull request if, and
only if, it

- Has tests
- Follows the code conventions

If you plan to implement a new feature or something that will take more
than a few minutes, please open an issue to make sure we don't work on
the same thing.
