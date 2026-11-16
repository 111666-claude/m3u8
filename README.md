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

## Applying a key rotation schedule

Event replays are often encrypted with a sequence of AES-128 keys instead
of a single key for the whole presentation. When the replay playlist
already lists its segments but has no `EXT-X-KEY` tags yet, describe the
rotation plan as contiguous `KeyInterval` objects and apply it with
`apply_key_schedule` (or the `playlist.apply_key_schedule` method):

``` python
import m3u8

playlist = m3u8.load('http://videoserver.com/replay.m3u8')

playlist.apply_key_schedule([
    m3u8.KeyInterval(100, 102, uri='keys/key0.bin', iv='0x000102030405060708090a0b0c0d0e0f'),
    m3u8.KeyInterval(102, 104, uri='keys/key1.bin', iv='0x101112131415161718191a1b1c1d1e1f'),
    m3u8.KeyInterval(104, 106, uri='keys/key2.bin', iv='0x202122232425262728292a2b2c2d2e2f'),
    m3u8.KeyInterval(106, 107, method='NONE'),  # switch back to clear media
])

print(playlist.dumps())  # one EXT-X-KEY tag at each switch point
```

Intervals are half-open and are given in Media Sequence Number order (pass
`axis='index'` to use zero-based segment indexes instead; `axis='auto'`,
the default, detects the unit). They must be sorted, contiguous and cover
every segment exactly once, so every segment has a well defined key. Each
segment gets the `Key` object of its interval, while its URI, duration,
byte range and Media Sequence Number are preserved; an interval with
`method='NONE'` switches back to clear media. The `IV` is optional for
AES-128 and must be written as `0x` followed by 32 hexadecimal digits.

Use `playlist.key_intervals()` (or `m3u8.key_intervals_from_playlist`) to
read the schedule back, including from a playlist that was serialized and
parsed again. `m3u8.KeyScheduleError` is raised for overlapping intervals,
gaps, out-of-bounds indexes, unsupported methods, missing key URIs and
malformed IVs; the playlist is left untouched when that happens.

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
