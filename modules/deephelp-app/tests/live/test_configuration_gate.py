import pytest

from deephelp_app.settings import Settings

pytestmark = pytest.mark.live


def test_explicit_live_configuration_gate():
    """Diagnostic only. M03 must supply an actual budget-enforcing live gateway."""
    Settings.from_env().model_copy(update={"mode": "live"}).validate_live()
