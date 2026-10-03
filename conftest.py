"""Workspace-level live options must be registered before pytest parses command-line flags."""

import socket
from decimal import Decimal, InvalidOperation
from ipaddress import ip_address

import pytest

from deephelp_app.errors import ConfigurationError
from deephelp_app.settings import Settings


def _allow_local_host(host):
    if host is None or host in {"localhost", b"localhost"}:
        return
    try:
        address = ip_address(host.decode() if isinstance(host, bytes) else host)
    except ValueError:
        address = None
    if address is None or not address.is_loopback:
        raise RuntimeError("Offline pytest blocks remote network; use an explicit live probe")


def _install_offline_network_guard(config):
    # Install before collection, including when --live only checks configuration.
    patch = pytest.MonkeyPatch()
    original_resolve = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def resolve(host, *args, **kwargs):
        _allow_local_host(host)
        return original_resolve(host, *args, **kwargs)

    def connect(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            _allow_local_host(address[0])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            _allow_local_host(address[0])
        return original_connect_ex(sock, address)

    patch.setattr(socket, "getaddrinfo", resolve)
    patch.setattr(socket.socket, "connect", connect)
    patch.setattr(socket.socket, "connect_ex", connect_ex)
    config._deephelp_network_patch = patch


def pytest_unconfigure(config):
    patch = getattr(config, "_deephelp_network_patch", None)
    if patch:
        patch.undo()


def pytest_addoption(parser):
    group = parser.getgroup("deephelp-live")
    group.addoption("--live", action="store_true", help="Enable explicit live configuration checks")
    group.addoption("--live-max-calls", type=int, default=0)
    group.addoption("--live-max-tokens", type=int, default=0)
    group.addoption("--live-max-cost", default="0")


def pytest_configure(config):
    _install_offline_network_guard(config)
    if not config.getoption("--live"):
        return
    try:
        settings = Settings.from_env().model_copy(update={"mode": "live"})
        settings.validate_live()
        calls = config.getoption("--live-max-calls")
        tokens = config.getoption("--live-max-tokens")
        cost = Decimal(config.getoption("--live-max-cost"))
        if (
            calls <= 0
            or calls > settings.live_max_calls
            or tokens <= 0
            or tokens > settings.live_max_tokens
            or not cost.is_finite()
            or cost <= 0
            or cost > settings.live_max_cost
        ):
            raise ConfigurationError(
                "CLI live budgets must be positive and within environment caps"
            )
    except (ConfigurationError, InvalidOperation) as exc:
        message = str(exc) if isinstance(exc, ConfigurationError) else "Invalid live cost budget"
        raise pytest.UsageError(message) from None


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live"):
        for item in items:
            if "live" in item.keywords:
                item.add_marker(
                    pytest.mark.skip(reason="Live requires explicit switches and budgets")
                )
