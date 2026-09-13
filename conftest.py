"""Workspace-level live options must be registered before pytest parses command-line flags."""

from decimal import Decimal, InvalidOperation

import pytest

from deephelp_app.errors import ConfigurationError
from deephelp_app.settings import Settings


def pytest_addoption(parser):
    group = parser.getgroup("deephelp-live")
    group.addoption("--live", action="store_true", help="Enable explicit live configuration checks")
    group.addoption("--live-max-calls", type=int, default=0)
    group.addoption("--live-max-tokens", type=int, default=0)
    group.addoption("--live-max-cost", default="0")


def pytest_configure(config):
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
