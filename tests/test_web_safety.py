import socket

import pytest

from adaptive_harness.tools.builtin import _validate_public_url


def test_http_rejects_localhost():
    with pytest.raises(ValueError):
        _validate_public_url("http://localhost/admin")


def test_http_rejects_private_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))],
    )
    with pytest.raises(ValueError):
        _validate_public_url("http://metadata.example/latest")
