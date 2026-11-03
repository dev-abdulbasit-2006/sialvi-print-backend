"""TEST-ONLY helpers. The product (shared/, admin/, user/, owner/) is cloud-only and HTTPS-only.

Unit tests start a licensing server inside the test process (loopback, plain HTTP, never leaves the PC),
so these subclasses relax ONLY the URL check. They live under tests/ and are never imported by the apps.
"""
from urllib.parse import urlparse

from owner.api import OwnerApi
from shared.license_client import LicenseClient, require_https


def _is_loopback_http(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == "http" and (p.hostname or "") in ("localhost", "127.0.0.1", "::1")


class LocalLicenseClient(LicenseClient):
    @staticmethod
    def _check_server_url(url):
        if not _is_loopback_http(url):
            require_https(url)


class LocalOwnerApi(OwnerApi):
    @staticmethod
    def _check_base_url(base):
        if not _is_loopback_http(base):
            OwnerApi._check_base_url(base)
