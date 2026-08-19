from __future__ import annotations

import pytest

from kendra.health.doctor import _http_ok


@pytest.mark.asyncio
async def test_http_ok_rejects_404(monkeypatch):
    class Response:
        status_code = 404

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            return Response()

    monkeypatch.setattr("kendra.health.doctor.httpx.AsyncClient", lambda **kwargs: Client())
    ok, status = await _http_ok("http://127.0.0.1/health")
    assert ok is False
    assert status == 404
