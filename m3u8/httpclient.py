import gzip
import ssl
import urllib.request
from urllib.parse import urljoin

from m3u8.variables import query_variables_from_uri


class DefaultHTTPClient:
    def __init__(self, proxies=None):
        self.proxies = proxies

    def download(self, uri, timeout=None, headers={}, verify_ssl=True):
        proxy_handler = urllib.request.ProxyHandler(self.proxies)
        https_handler = HTTPSHandler(verify_ssl=verify_ssl)
        opener = urllib.request.build_opener(proxy_handler, https_handler)
        opener.addheaders = headers.items()
        resource = opener.open(uri, timeout=timeout)
        final_uri = resource.geturl()
        base_uri = urljoin(final_uri, ".")

        if resource.info().get("Content-Encoding") == "gzip":
            content = gzip.decompress(resource.read()).decode(
                resource.headers.get_content_charset(failobj="utf-8")
            )
        else:
            content = resource.read().decode(
                resource.headers.get_content_charset(failobj="utf-8")
            )
        # The third element carries the query parameters of the final
        # URI (including redirects) for EXT-X-DEFINE QUERYPARAM. Custom
        # HTTP clients that still return a 2-tuple are supported.
        return content, base_uri, query_variables_from_uri(final_uri)


class HTTPSHandler:
    def __new__(self, verify_ssl=True):
        context = ssl.create_default_context()
        if not verify_ssl:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return urllib.request.HTTPSHandler(context=context)
