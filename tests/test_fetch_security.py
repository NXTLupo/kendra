from __future__ import annotations

import socket

import pytest

from kendra.research.fetch import assert_public_http_url


def test_blocks_localhost():
    with pytest.raises(ValueError):
        assert_public_http_url("http://localhost/admin")


def test_blocks_private_resolution(monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="non-public"):
        assert_public_http_url("https://example.test/admin")


def test_allows_public_resolution(monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert_public_http_url("https://example.test/")
