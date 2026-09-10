from __future__ import annotations

import os
from typing import Any

from app.config import settings


def _resolve_value(*candidates: str) -> str:
    for value in candidates:
        if value and value.strip():
            return value.strip()
    return ""


def configure_network_environment() -> dict[str, Any]:
    """Apply proxy and SSL environment settings for outbound network clients.

    Resolution order for each key:
    1) explicit app settings value
    2) existing uppercase environment value
    3) existing lowercase environment value
    """

    http_proxy = _resolve_value(settings.HTTP_PROXY, os.getenv("HTTP_PROXY", ""), os.getenv("http_proxy", ""))
    https_proxy = _resolve_value(settings.HTTPS_PROXY, os.getenv("HTTPS_PROXY", ""), os.getenv("https_proxy", ""))
    no_proxy = _resolve_value(settings.NO_PROXY, os.getenv("NO_PROXY", ""), os.getenv("no_proxy", ""))

    if http_proxy:
        os.environ["HTTP_PROXY"] = http_proxy
        os.environ["http_proxy"] = http_proxy
    if https_proxy:
        os.environ["HTTPS_PROXY"] = https_proxy
        os.environ["https_proxy"] = https_proxy
    if no_proxy:
        os.environ["NO_PROXY"] = no_proxy
        os.environ["no_proxy"] = no_proxy

    if settings.SSL_CA_CERT_PATH:
        cert_path = settings.SSL_CA_CERT_PATH.strip()
        os.environ["SSL_CERT_FILE"] = cert_path
        os.environ["REQUESTS_CA_BUNDLE"] = cert_path
        os.environ["CURL_CA_BUNDLE"] = cert_path

    if not settings.SSL_VERIFY:
        os.environ["PYTHONHTTPSVERIFY"] = "0"

    return {
        "has_http_proxy": bool(http_proxy),
        "has_https_proxy": bool(https_proxy),
        "has_no_proxy": bool(no_proxy),
        "has_custom_ca_bundle": bool(settings.SSL_CA_CERT_PATH.strip()),
        "ssl_verify": settings.SSL_VERIFY,
    }
