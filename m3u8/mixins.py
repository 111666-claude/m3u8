from os.path import dirname
from urllib.parse import urljoin, urlsplit

from m3u8.variables import substitute


class BasePathMixin:
    # Terminal variable scope for the owning Playlist. None means the
    # object was created standalone and has no EXT-X-DEFINE context, so
    # URIs are returned unchanged.
    variables = None

    @property
    def absolute_uri(self):
        if self.uri is None:
            return None

        ret = urljoin(self.base_uri, self.uri)
        if self.base_uri:
            base_uri_parts = urlsplit(self.base_uri)
            if (not base_uri_parts.scheme) and (not base_uri_parts.netloc):
                return ret

        if not urlsplit(ret).scheme:
            raise ValueError("There can not be `absolute_uri` with no `base_uri` set")

        return ret

    @property
    def resolved_uri(self):
        """
        The URI with every ``{$VariableName}`` reference expanded
        according to the Playlist's EXT-X-DEFINE scope. URIs without a
        reference are returned unchanged; an unresolvable reference
        raises even when the Playlist declared no variables. Unlike
        ``absolute_uri`` it never joins against ``base_uri``.
        """
        if self.uri is None:
            return None
        if not self.variables and "{$" not in self.uri:
            return self.uri
        return substitute(self.uri, self.variables or {})

    @property
    def resolved_absolute_uri(self):
        """Like ``absolute_uri``, but computed from the resolved URI."""
        if self.uri is None:
            return None
        if self.base_uri is None:
            return self.resolved_uri
        return urljoin(self.base_uri, self.resolved_uri)

    @property
    def base_path(self):
        if self.uri is None:
            return None
        return dirname(self.get_path_from_uri())

    def get_path_from_uri(self):
        """Some URIs have a slash in the query string."""
        return self.uri.split("?")[0]

    @base_path.setter
    def base_path(self, newbase_path):
        if self.uri is not None:
            if not self.base_path:
                self.uri = f"{newbase_path}/{self.uri}"
            else:
                self.uri = self.uri.replace(self.base_path, newbase_path)


class GroupedBasePathMixin:
    def _set_base_uri(self, new_base_uri):
        for item in self:
            item.base_uri = new_base_uri

    base_uri = property(None, _set_base_uri)

    def _set_base_path(self, newbase_path):
        for item in self:
            item.base_path = newbase_path

    base_path = property(None, _set_base_path)
