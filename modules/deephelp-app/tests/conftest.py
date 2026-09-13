"""Tests permit synthetic loopback HTTP only; external DNS/connections fail closed."""

import socket
from ipaddress import ip_address

import pytest


@pytest.fixture(autouse=True)
def offline_network(monkeypatch):
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def allowed(host):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host == "localhost":
            return True
        try:
            return ip_address(host).is_loopback
        except ValueError:
            return False

    def connect(sock, address):
        if isinstance(address, tuple) and not allowed(address[0]):
            raise AssertionError("External network disabled in M01 tests")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if isinstance(address, tuple) and not allowed(address[0]):
            raise AssertionError("External network disabled in M01 tests")
        return original_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not allowed(host):
            raise AssertionError("External DNS disabled in M01 tests")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture
def message():
    return {
        "channel": "local",
        "session_id": "session-1",
        "message_id": "message-1",
        "raw_text": "合成测试：查询订单",
        "occurred_at": "2026-09-13T01:00:00+08:00",
    }
