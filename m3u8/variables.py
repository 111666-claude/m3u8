# Copyright 2014 Globo.com Player authors. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

"""
Support for the EXT-X-DEFINE tag and Playlist Variable Substitution.

See sections 4.3, 4.4.2.3 and 6.3.1 of the HLS 2nd edition draft:
https://datatracker.ietf.org/doc/html/draft-pantos-hls-rfc8216bis

A Variable Reference has the form ``{$VariableName}``. Variable names are
case-sensitive and may only contain letters, digits, ``-`` and ``_``.
"""

import re
from urllib.parse import parse_qs

# A Variable Reference: "{$" followed by a Variable Name followed by "}".
# Anything between the braces is captured so an illegal name or a
# malformed reference can be reported precisely.
VARIABLE_REFERENCE_PATTERN = re.compile(r"\{\$([^}]*)\}")

# Variable Name grammar from section 4.4.2.3: the quoted-string is
# limited to [a-z], [A-Z], [0-9], '-' and '_'. Names beginning with an
# underscore are reserved, so a Playlist-declared name must not start
# with one.
VARIABLE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9-][a-zA-Z0-9-_]*$")
# Characters that may not appear in a value substituted into a
# quoted-string AttributeValue (section 4.2). Braces are rejected as
# well so substituted values can never look like Variable References.
FORBIDDEN_SUBSTITUTION_CHARACTERS = ('"', "\r", "\n", "{", "}")

# Source attributes of an EXT-X-DEFINE tag.
DEFINE_NAME = "NAME"
DEFINE_IMPORT = "IMPORT"
DEFINE_QUERY_PARAM = "QUERYPARAM"


class VariableSubstitutionError(Exception):
    """Base class for every error raised while resolving EXT-X-DEFINE."""


class InvalidVariableNameError(VariableSubstitutionError):
    """A Variable Name does not match the grammar allowed by the spec."""


class DuplicateVariableError(VariableSubstitutionError):
    """The same Variable Name is declared more than once in a Playlist."""


class UndefinedVariableError(VariableSubstitutionError):
    """A ``{$name}`` reference has no preceding EXT-X-DEFINE declaration."""


class CyclicVariableReferenceError(VariableSubstitutionError):
    """Variable definitions refer to each other through a cycle."""


class IllegalSubstitutionValueError(VariableSubstitutionError):
    """A substituted value contains a character forbidden in a URI value."""


class VariableDefinitionError(VariableSubstitutionError):
    """An EXT-X-DEFINE tag is malformed or cannot be resolved."""


def is_valid_variable_name(name):
    """Return True when ``name`` is a legal, non-reserved Variable Name."""
    return bool(name) and VARIABLE_NAME_PATTERN.match(name) is not None


def substitute(value, variables):
    """
    Expand every ``{$VariableName}`` reference in ``value`` using the
    already-resolved ``variables`` mapping (terminal values only).

    Raises InvalidVariableNameError for a malformed reference,
    UndefinedVariableError when the name has no EXT-X-DEFINE declaration
    and IllegalSubstitutionValueError when the expanded value contains a
    forbidden character or the URI carries an unclosed reference.
    """
    if value is None:
        return None

    def _replace(match):
        name = match.group(1)
        if not is_valid_variable_name(name):
            raise InvalidVariableNameError(
                'Illegal Variable Name "%s" in reference "%s"' % (name, match.group(0))
            )
        if name not in variables:
            raise UndefinedVariableError(
                'Missing preceding EXT-X-DEFINE tag for Variable Reference: "%s"' % name
            )
        replacement = variables[name]
        _check_substitution_value(name, replacement)
        return replacement

    expanded = VARIABLE_REFERENCE_PATTERN.sub(_replace, value)

    # A leftover brace means the URI held a malformed reference such as
    # "{$NAME" without a closing brace.
    if "{" in expanded or "}" in expanded:
        raise IllegalSubstitutionValueError(
            "Malformed Variable Reference in URI value: %r" % value
        )
    return expanded


def resolve_variables(definitions, imported=None, query_variables=None):
    """
    Build the terminal variable scope of a Playlist.

    ``definitions`` is the ordered list of raw EXT-X-DEFINE dictionaries
    produced by the parser, each with exactly one source attribute:

      * ``{"name": "CDN", "value": "https://..."}``
      * ``{"import": "CDN"}``
      * ``{"query_param": "CDN"}``

    ``imported`` holds variables inherited from the Multivariant
    Playlist and ``query_variables`` the percent-decoded query parameters
    of the Playlist URI.

    Precedence, highest to lowest:

      1. NAME/VALUE definitions local to this Playlist
      2. IMPORT definitions resolved against the Multivariant Playlist
      3. QUERYPARAM definitions resolved against the Playlist URI query

    Every malformed declaration, duplicate name, unresolved
    IMPORT/QUERYPARAM entry, cyclic local definition and undefined
    reference raises a VariableSubstitutionError subclass.
    """
    return _Resolver(definitions, imported, query_variables).resolve()


class _Resolver:
    def __init__(self, definitions, imported, query_variables):
        self._definitions = definitions
        self._imported = imported or {}
        self._query_variables = query_variables or {}
        self._local_raw = {}
        self._order = []
        self._scope = {}
        self._resolved = set()
        self._pending = []

    def resolve(self):
        self._validate_and_index()

        # Lowest precedence first; local definitions (resolved below)
        # override these entries.
        self._scope.update(self._imported)
        self._scope.update(self._query_variables)

        # A local definition may reference any preceding variable: an
        # imported one, a query-sourced one, or an earlier local
        # definition. References to later declarations fail as undefined
        # because those names are not in the scope yet, matching the
        # "EXT-X-DEFINE tag that precedes the Variable Reference" rule.
        for name in self._order:
            self._resolve_local(name)
        return self._scope

    def _validate_and_index(self):
        seen = set()
        for definition in self._definitions:
            name, kind, raw_value = _read_definition(definition)

            if not is_valid_variable_name(name):
                raise InvalidVariableNameError(
                    'Illegal Variable Name "%s" in EXT-X-DEFINE' % name
                )
            if name in seen:
                raise DuplicateVariableError(
                    'EXT-X-DEFINE duplicate Variable Name declarations: "%s"' % name
                )
            seen.add(name)

            if kind == DEFINE_NAME:
                self._local_raw[name] = raw_value
                self._order.append(name)
            elif kind == DEFINE_IMPORT:
                if name not in self._imported:
                    raise VariableDefinitionError(
                        "EXT-X-DEFINE IMPORT attribute not found in the "
                        'Multivariant Playlist: "%s"' % name
                    )
            else:  # QUERYPARAM
                if name not in self._query_variables:
                    raise VariableDefinitionError(
                        'EXT-X-DEFINE QUERYPARAM "%s" does not match any '
                        "query parameter in the Playlist URI" % name
                    )
                _check_query_value(name, self._query_variables[name])

    def _resolve_local(self, name):
        if name in self._resolved:
            return self._scope[name]
        if name in self._pending:
            chain = " -> ".join(self._pending + [name])
            raise CyclicVariableReferenceError(
                "Cyclic variable reference detected: %s" % chain
            )

        self._pending.append(name)
        try:
            raw_value = self._local_raw[name]

            def _replace(match):
                referenced = match.group(1)
                if not is_valid_variable_name(referenced):
                    raise InvalidVariableNameError(
                        'Illegal Variable Name "%s" referenced by "%s"'
                        % (referenced, name)
                    )
                if referenced in self._resolved:
                    value = self._scope[referenced]
                elif referenced in self._local_raw:
                    if referenced in self._pending:
                        # Re-entering a name on the resolution stack
                        # closes the loop; recurse once to surface the
                        # cycle error.
                        value = self._resolve_local(referenced)
                    else:
                        # A local name that has not entered resolution
                        # is a forward declaration, which has no
                        # EXT-X-DEFINE tag preceding this reference.
                        raise UndefinedVariableError(
                            'Variable "%s" references variable "%s" '
                            "declared by a later EXT-X-DEFINE tag" % (name, referenced)
                        )
                elif referenced in self._scope:
                    # Imported or query-sourced terminal value.
                    value = self._scope[referenced]
                else:
                    raise UndefinedVariableError(
                        'Variable "%s" references undefined variable '
                        '"%s"' % (name, referenced)
                    )
                _check_substitution_value(referenced, value)
                return value

            expanded = VARIABLE_REFERENCE_PATTERN.sub(_replace, raw_value)
            if "{" in expanded or "}" in expanded:
                raise IllegalSubstitutionValueError(
                    "Malformed Variable Reference in value of variable "
                    '"%s": %r' % (name, raw_value)
                )
            _check_substitution_value(name, expanded)
        finally:
            self._pending.remove(name)

        self._scope[name] = expanded
        self._resolved.add(name)
        return expanded


def _read_definition(definition):
    """Normalize one parsed EXT-X-DEFINE dictionary into a 3-tuple."""
    has_name = "name" in definition
    has_import = "import" in definition
    has_query_param = "query_param" in definition

    if sum((has_name, has_import, has_query_param)) != 1:
        raise VariableDefinitionError(
            "EXT-X-DEFINE must contain exactly one of NAME, IMPORT or QUERYPARAM"
        )

    if has_name:
        if "value" not in definition:
            raise VariableDefinitionError(
                "EXT-X-DEFINE with NAME must also carry a VALUE attribute"
            )
        return definition["name"], DEFINE_NAME, definition["value"]

    if has_import:
        return definition["import"], DEFINE_IMPORT, None

    return definition["query_param"], DEFINE_QUERY_PARAM, None


def _check_substitution_value(name, value):
    for char in FORBIDDEN_SUBSTITUTION_CHARACTERS:
        if char in value:
            raise IllegalSubstitutionValueError(
                'Value of variable "%s" contains a forbidden character '
                "%r: %r" % (name, char, value)
            )


def _check_query_value(name, value):
    # Section 4.4.2.3: the percent-decoded QUERYPARAM value MUST NOT
    # contain any character disallowed in quoted-strings.
    for char in ('"', "\r", "\n"):
        if char in value:
            raise IllegalSubstitutionValueError(
                'Decoded query parameter "%s" contains a character '
                "disallowed in quoted-strings: %r" % (name, value)
            )


def query_variables_from_uri(uri):
    """
    Extract percent-decoded query parameters from a Playlist URI.

    When a parameter appears more than once the first value is used, as
    permitted by section 4.4.2.3. Returns an empty mapping when there is
    no query string. Any URI fragment is ignored.
    """
    if not uri or "?" not in uri:
        return {}
    query = uri.split("?", 1)[1]
    # A fragment, if present, always comes after the query component.
    query = query.split("#", 1)[0]
    if not query:
        return {}
    parsed = parse_qs(query, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}
