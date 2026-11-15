# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import playlists
import pytest

import m3u8
from m3u8 import (
    PathwaySelector,
    SteeringManifestError,
    SteeringRequestError,
    parse_steering_manifest,
)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class SteeringRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        server = self.server
        path = urlsplit(self.path).path
        server.hits[path] = server.hits.get(path, 0) + 1
        route = server.routes.get(path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        if callable(route):
            route = route()
        status, body = route
        body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if status < 400:
            self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def steering_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SteeringRequestHandler)
    server.routes = {}
    server.hits = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def steering_manifest(ttl=10, priority=("CDN-B", "CDN-A"), reload_uri=None, version=1):
    manifest = {"VERSION": version, "TTL": ttl, "PATHWAY-PRIORITY": list(priority)}
    if reload_uri is not None:
        manifest["RELOAD-URI"] = reload_uri
    return json.dumps(manifest)


def make_master(server):
    uri = f"http://127.0.0.1:{server.server_address[1]}/live/master.m3u8"
    return m3u8.loads(playlists.CONTENT_STEERING_PLAYLIST, uri=uri)


def make_selector(server, clock=None, **kwargs):
    return PathwaySelector(make_master(server), clock=clock or FakeClock(), **kwargs)


def test_preferred_pathway_and_variants(steering_server):
    steering_server.routes["/steering"] = (200, steering_manifest())
    selector = make_selector(steering_server)

    assert selector.preferred_pathway == "CDN-B"

    variants = selector.variants()
    assert len(variants) == 2
    assert all(v.stream_info.pathway_id == "CDN-B" for v in variants)
    assert {v.stream_info.bandwidth for v in variants} == {1280000, 7680000}


def test_server_uri_is_resolved_against_master_base_uri(steering_server):
    steering_server.routes["/steering"] = (200, steering_manifest())
    selector = make_selector(steering_server)

    assert selector.preferred_pathway == "CDN-B"

    # SERVER-URI="/steering?video=00012" resolved against
    # http://127.0.0.1:<port>/live/ -> http://127.0.0.1:<port>/steering?video=00012
    assert steering_server.hits == {"/steering": 1}


def test_ttl_caching_and_priority_change(steering_server):
    steering_server.routes["/steering"] = (200, steering_manifest(ttl=10))
    clock = FakeClock()
    selector = make_selector(steering_server, clock=clock)

    assert selector.preferred_pathway == "CDN-B"
    assert steering_server.hits["/steering"] == 1

    # still within TTL: cached, no new request
    clock.advance(5)
    assert selector.preferred_pathway == "CDN-B"
    assert steering_server.hits["/steering"] == 1

    # server now prefers CDN-A; before TTL expiry the cache still wins
    steering_server.routes["/steering"] = (
        200,
        steering_manifest(ttl=10, priority=("CDN-A", "CDN-B")),
    )
    clock.advance(4)
    assert selector.preferred_pathway == "CDN-B"
    assert steering_server.hits["/steering"] == 1

    # TTL expired: reload picks up the new priority
    clock.advance(2)
    assert selector.preferred_pathway == "CDN-A"
    assert steering_server.hits["/steering"] == 2


def test_reload_uri_relative_resolution(steering_server):
    steering_server.routes["/steering"] = (
        200,
        steering_manifest(ttl=5, reload_uri="v2/steering-reload.json"),
    )
    steering_server.routes["/v2/steering-reload.json"] = (
        200,
        steering_manifest(ttl=5, priority=("CDN-A", "CDN-B")),
    )
    clock = FakeClock()
    selector = make_selector(steering_server, clock=clock)

    assert selector.preferred_pathway == "CDN-B"
    clock.advance(6)
    assert selector.preferred_pathway == "CDN-A"
    assert steering_server.hits["/steering"] == 1
    assert steering_server.hits["/v2/steering-reload.json"] == 1


def test_server_failure_keeps_previous_selection(steering_server):
    steering_server.routes["/steering"] = (200, steering_manifest(ttl=10))
    clock = FakeClock()
    selector = make_selector(steering_server, clock=clock)

    assert selector.preferred_pathway == "CDN-B"

    steering_server.routes["/steering"] = (500, "boom")
    clock.advance(11)
    assert selector.preferred_pathway == "CDN-B"
    assert isinstance(selector.last_error, SteeringRequestError)
    assert [v.stream_info.pathway_id for v in selector.variants()] == ["CDN-B"] * 2


def test_initial_failure_falls_back_to_master_default(steering_server):
    steering_server.routes["/steering"] = (500, "boom")
    selector = make_selector(steering_server)

    # PATHWAY-ID of the EXT-X-CONTENT-STEERING tag is the default
    assert selector.preferred_pathway == "CDN-A"
    assert isinstance(selector.last_error, SteeringRequestError)
    assert all(v.stream_info.pathway_id == "CDN-A" for v in selector.variants())


def test_request_timeout_falls_back_to_master_default(steering_server):
    def slow_route():
        time.sleep(1.0)
        return 200, steering_manifest()

    steering_server.routes["/steering"] = slow_route
    selector = make_selector(steering_server, timeout=0.1)

    assert selector.preferred_pathway == "CDN-A"
    assert isinstance(selector.last_error, SteeringRequestError)


def test_recovery_after_failure(steering_server):
    steering_server.routes["/steering"] = (500, "boom")
    clock = FakeClock()
    selector = make_selector(steering_server, clock=clock)
    assert selector.preferred_pathway == "CDN-A"

    steering_server.routes["/steering"] = (200, steering_manifest(ttl=10))
    clock.advance(1)
    assert selector.preferred_pathway == "CDN-B"
    assert selector.last_error is None


def test_master_playlist_is_not_modified(steering_server):
    steering_server.routes["/steering"] = (200, steering_manifest())
    master = make_master(steering_server)
    before = master.dumps()

    selector = PathwaySelector(master, clock=FakeClock())
    assert selector.preferred_pathway == "CDN-B"
    assert len(selector.variants()) == 2
    assert len(selector.variants("CDN-A")) == 2

    assert master.dumps() == before
    assert master.content_steering.uri == "/steering?video=00012"


def test_variants_for_explicit_pathway(steering_server):
    steering_server.routes["/steering"] = (200, steering_manifest())
    selector = make_selector(steering_server)

    assert len(selector.variants("CDN-A")) == 2
    assert len(selector.variants("CDN-B")) == 2
    assert selector.available_pathways == ["CDN-A", "CDN-B"]
    assert selector.default_pathway == "CDN-A"


def test_master_without_content_steering_raises():
    master = m3u8.loads(playlists.SIMPLE_PLAYLIST)
    with pytest.raises(ValueError):
        PathwaySelector(master)


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        "[1, 2, 3]",
        json.dumps({"TTL": 10, "PATHWAY-PRIORITY": ["CDN-A"]}),  # no VERSION
        json.dumps({"VERSION": 2, "PATHWAY-PRIORITY": ["CDN-A"]}),  # unsupported
        json.dumps({"VERSION": 1, "TTL": -1, "PATHWAY-PRIORITY": ["CDN-A"]}),
        json.dumps({"VERSION": 1, "TTL": "10", "PATHWAY-PRIORITY": ["CDN-A"]}),
        json.dumps({"VERSION": 1, "PATHWAY-PRIORITY": []}),  # empty priority
        json.dumps({"VERSION": 1, "PATHWAY-PRIORITY": "CDN-A"}),  # not a list
        json.dumps({"VERSION": 1, "PATHWAY-PRIORITY": ["CDN-A", "CDN-A"]}),  # dup
        json.dumps({"VERSION": 1, "PATHWAY-PRIORITY": ["CDN-C"]}),  # unknown
        json.dumps(
            {
                "VERSION": 1,
                "PATHWAY-PRIORITY": ["CDN-A"],
                "PATHWAY-CLONES": [],
            }
        ),  # cloning not implemented
    ],
)
def test_invalid_manifests_have_clear_errors(steering_server, body):
    steering_server.routes["/steering"] = (200, body)
    selector = make_selector(steering_server)

    with pytest.raises(SteeringManifestError):
        _ = selector.preferred_pathway


def test_parse_steering_manifest_defaults():
    manifest = parse_steering_manifest(
        json.dumps({"VERSION": 1, "PATHWAY-PRIORITY": ["CDN-A"]}),
        "http://example.com/steering",
        {"CDN-A"},
    )
    assert manifest.ttl == 300
    assert manifest.reload_uri is None
    assert manifest.pathway_priority == ["CDN-A"]


def test_parse_steering_manifest_resolves_relative_reload_uri():
    manifest = parse_steering_manifest(
        json.dumps(
            {
                "VERSION": 1,
                "TTL": 5,
                "RELOAD-URI": "../reload.json",
                "PATHWAY-PRIORITY": ["CDN-A"],
            }
        ),
        "http://example.com/a/b/steering",
        {"CDN-A"},
    )
    assert manifest.reload_uri == "http://example.com/a/reload.json"
