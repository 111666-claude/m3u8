# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import pytest

from m3u8.parser import parse
from m3u8.variables import (
    CyclicVariableReferenceError,
    DuplicateVariableError,
    IllegalSubstitutionValueError,
    InvalidVariableNameError,
    UndefinedVariableError,
    VariableDefinitionError,
    is_valid_variable_name,
    query_variables_from_uri,
    resolve_variables,
    substitute,
)

# ---------------------------------------------------------------------------
# Parser: EXT-X-DEFINE is captured in declaration order and raw form
# ---------------------------------------------------------------------------


def test_parser_collects_define_declarations():
    content = """#EXTM3U
#EXT-X-DEFINE:NAME="CDN",VALUE="https://cdn.example.com"
#EXT-X-DEFINE:IMPORT="TOKEN"
#EXT-X-DEFINE:QUERYPARAM="REGION"
#EXT-X-TARGETDURATION:5
#EXTINF:5,
{$CDN}/seg.ts
"""
    data = parse(content)
    assert data["defines"] == [
        {"name": "CDN", "value": "https://cdn.example.com"},
        {"import": "TOKEN"},
        {"query_param": "REGION"},
    ]


def test_parser_keeps_segment_uri_raw():
    content = """#EXTM3U
#EXT-X-DEFINE:NAME="CDN",VALUE="https://cdn.example.com"
#EXTINF:5,
{$CDN}/seg.ts
"""
    data = parse(content)
    assert data["segments"][0]["uri"] == "{$CDN}/seg.ts"
    # No EXT-X-KEY tag: the parser records the unencrypted None marker.
    assert data["keys"] == [None]


def test_parser_keeps_key_and_map_uris_raw():
    content = """#EXTM3U
#EXT-X-DEFINE:NAME="A",VALUE="https://a.example"
#EXT-X-KEY:METHOD=AES-128,URI="{$A}/key.bin"
#EXT-X-MAP:URI="{$A}/init.mp4"
#EXTINF:5,
{$A}/seg.ts
"""
    data = parse(content)
    assert data["keys"][0]["uri"] == "{$A}/key.bin"
    assert data["segment_map"][0]["uri"] == "{$A}/init.mp4"


def test_parser_without_defines_has_empty_list():
    data = parse("#EXTM3U\n#EXTINF:5,\nhttp://example.com/a.ts\n")
    assert data["defines"] == []


# ---------------------------------------------------------------------------
# Name grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["A", "Z9", "1ABC", "a-b_c", "CDN-1", "X_1", "abc", "A1-B2_C3"],
)
def test_valid_variable_names(name):
    assert is_valid_variable_name(name)


@pytest.mark.parametrize(
    "name",
    ["_RESERVED", "_", "", "HAS SPACE", "A/B", "A.B", "a,b", "{$X}", "a:b"],
)
def test_invalid_variable_names(name):
    assert not is_valid_variable_name(name)


# ---------------------------------------------------------------------------
# substitute() on a resolved terminal scope
# ---------------------------------------------------------------------------


def test_substitute_expands_references():
    scope = {"CDN": "https://cdn.example.com", "REGION": "eu"}
    assert substitute("{$CDN}/{$REGION}/a.ts", scope) == (
        "https://cdn.example.com/eu/a.ts"
    )


def test_substitute_keeps_literal_dollar_and_brace_text():
    # Only the exact {$name} form is a reference
    assert substitute("no refs here", {}) == "no refs here"
    assert substitute("price $5", {}) == "price $5"


def test_substitute_none_passes_through():
    assert substitute(None, {"A": "x"}) is None


def test_substitute_undefined_reference_raises():
    with pytest.raises(UndefinedVariableError, match="MISSING"):
        substitute("{$MISSING}/a.ts", {})


def test_substitute_illegal_reference_name_raises():
    with pytest.raises(InvalidVariableNameError):
        substitute("{$_RESERVED}/a.ts", {"_RESERVED": "x"})
    with pytest.raises(InvalidVariableNameError):
        substitute("{$BAD NAME}/a.ts", {})


@pytest.mark.parametrize("bad_value", ['a"b', "a\rb", "a\nb", "a{b", "a}b"])
def test_substitute_rejects_forbidden_characters(bad_value):
    with pytest.raises(IllegalSubstitutionValueError):
        substitute("{$X}", {"X": bad_value})


def test_substitute_rejects_unclosed_reference():
    with pytest.raises(IllegalSubstitutionValueError):
        substitute("{$X/a.ts", {"X": "x"})


# ---------------------------------------------------------------------------
# resolve_variables(): sources, precedence, nesting
# ---------------------------------------------------------------------------


def test_resolve_local_name_and_value():
    scope = resolve_variables(
        [
            {"name": "HOST", "value": "https://h.example"},
            {"name": "PATH", "value": "{$HOST}/v"},
        ]
    )
    assert scope == {
        "HOST": "https://h.example",
        "PATH": "https://h.example/v",
    }


def test_resolve_import_and_queryparam():
    scope = resolve_variables(
        [{"import": "CDN"}, {"query_param": "REGION"}],
        imported={"CDN": "https://master.example"},
        query_variables={"REGION": "apac"},
    )
    assert scope == {
        "CDN": "https://master.example",
        "REGION": "apac",
    }


def test_local_name_overrides_imported_and_query_sources():
    scope = resolve_variables(
        [{"name": "CDN", "value": "https://local.example"}],
        imported={"CDN": "https://master.example"},
        query_variables={"CDN": "from-query"},
    )
    assert scope["CDN"] == "https://local.example"


def test_local_value_can_reference_imported_and_query_variables():
    scope = resolve_variables(
        [{"name": "ASSET", "value": "{$CDN}/{$REGION}/asset"}],
        imported={"CDN": "https://master.example"},
        query_variables={"REGION": "eu"},
    )
    assert scope["ASSET"] == "https://master.example/eu/asset"


def test_empty_value_is_allowed():
    assert resolve_variables([{"name": "EMPTY", "value": ""}]) == {"EMPTY": ""}


def test_forward_reference_is_undefined():
    with pytest.raises(UndefinedVariableError):
        resolve_variables([{"name": "A", "value": "{$B}"}, {"name": "B", "value": "x"}])


def test_self_reference_is_a_cycle():
    with pytest.raises(CyclicVariableReferenceError):
        resolve_variables([{"name": "A", "value": "{$A}"}])


def test_missing_import_raises():
    with pytest.raises(VariableDefinitionError, match="IMPORT"):
        resolve_variables([{"import": "ABSENT"}], imported={})


def test_missing_query_parameter_raises():
    with pytest.raises(VariableDefinitionError, match="QUERYPARAM"):
        resolve_variables([{"query_param": "REGION"}], query_variables={})


def test_duplicate_names_raise():
    with pytest.raises(DuplicateVariableError):
        resolve_variables([{"name": "A", "value": "1"}, {"name": "A", "value": "2"}])


def test_duplicate_across_source_kinds_raises():
    with pytest.raises(DuplicateVariableError):
        resolve_variables(
            [{"name": "A", "value": "1"}, {"query_param": "A"}],
            query_variables={"A": "q"},
        )


@pytest.mark.parametrize(
    "bad_name",
    ["_RESERVED", "", "A/B", "HAS SPACE"],
)
def test_illegal_names_in_definitions_raise(bad_name):
    with pytest.raises(InvalidVariableNameError):
        resolve_variables([{"name": bad_name, "value": "x"}])
    with pytest.raises(InvalidVariableNameError):
        resolve_variables([{"import": bad_name}], imported={bad_name: "x"})


def test_tag_with_multiple_source_attributes_raises():
    with pytest.raises(VariableDefinitionError):
        resolve_variables([{"name": "A", "value": "x", "import": "A"}])


def test_tag_without_any_source_attribute_raises():
    with pytest.raises(VariableDefinitionError):
        resolve_variables([{"value": "x"}])


def test_name_without_value_raises():
    with pytest.raises(VariableDefinitionError):
        resolve_variables([{"name": "A"}])


def test_decoded_query_value_with_quote_raises():
    with pytest.raises(IllegalSubstitutionValueError):
        resolve_variables([{"query_param": "Q"}], query_variables={"Q": 'a"b'})


def test_query_variables_from_uri_percent_decodes():
    assert query_variables_from_uri(
        "https://h/p.m3u8?REGION=eu%20west&token=a%26b"
    ) == {"REGION": "eu west", "token": "a&b"}


def test_query_variables_first_value_when_repeated():
    assert query_variables_from_uri("https://h/p?a=1&a=2") == {"a": "1"}


def test_query_variables_blank_parameter_is_empty_string():
    assert query_variables_from_uri("https://h/p?blank=") == {"blank": ""}


def test_query_variables_without_query_string():
    assert query_variables_from_uri("https://h/p") == {}
    assert query_variables_from_uri("") == {}


def test_query_variables_ignore_uri_fragment():
    assert query_variables_from_uri("https://h/p?a=1#frag") == {"a": "1"}


def test_parser_data_feeds_the_resolver_and_surfaces_cycles():
    # The parser stays permissive; validation happens at model level,
    # covered in test_model_variables.py. Here we only guarantee the
    # parser data structure is what resolve_variables consumes.
    data = parse('#EXTM3U\n#EXT-X-DEFINE:NAME="A",VALUE="{$A}"\n#EXTINF:5,\nseg.ts\n')
    with pytest.raises(CyclicVariableReferenceError):
        resolve_variables(data["defines"])
