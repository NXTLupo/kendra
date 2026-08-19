from __future__ import annotations

import pytest

from kendra.connectivity import assert_loopback_host, assert_loopback_http_url
from kendra.llm import LlamaCppClient


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/v1",
        "http://localhost:8080/v1",
        "http://[::1]:8080/v1",
    ],
)
def test_loopback_urls_are_allowed(url):
    assert_loopback_http_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8080/v1",
        "http://example.com/v1",
        "http://user:secret@127.0.0.1:8080/v1",
    ],
)
def test_nonlocal_or_credentialed_urls_are_rejected(url):
    with pytest.raises(ValueError):
        assert_loopback_http_url(url)


def test_nonloopback_webots_host_is_rejected():
    with pytest.raises(ValueError):
        assert_loopback_host("192.0.2.1")


def test_llm_client_rejects_hosted_endpoint(settings):
    settings.data["llm"]["base_url"] = "https://api.example.com/v1"
    with pytest.raises(ValueError, match="loopback"):
        LlamaCppClient(settings)
