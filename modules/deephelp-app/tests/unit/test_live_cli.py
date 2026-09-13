import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "case,expected_exit,diagnostic",
    [
        ("missing_key", 4, "DEEPHELP_MODEL_API_KEY"),
        ("over_budget", 4, "CLI live budgets"),
        ("missing_cli_budget", 4, "CLI live budgets"),
        ("configured_synthetic", 0, "1 passed"),
    ],
)
def test_root_live_cli_recognizes_options_and_gates_before_execution(
    case, expected_exit, diagnostic
):
    # Child process receives only synthetic credentials. The live test checks config, not a model.
    env = {name: value for name, value in os.environ.items() if not name.startswith("DEEPHELP_")}
    env.update(
        {
            "DEEPHELP_ENABLE_LIVE": "true",
            "DEEPHELP_MODEL_API_KEY": "synthetic-key",
            "DEEPHELP_MODEL_BASE_URL": "https://synthetic.invalid",
            "DEEPHELP_LIVE_MODEL": "synthetic",
            "DEEPHELP_LIVE_MAX_CALLS": "2",
            "DEEPHELP_LIVE_MAX_TOKENS": "20",
            "DEEPHELP_LIVE_MAX_COST": "1",
        }
    )
    flags = ["--live-max-calls", "1", "--live-max-tokens", "10", "--live-max-cost", "0.5"]
    if case == "missing_key":
        env.pop("DEEPHELP_MODEL_API_KEY")
    elif case == "over_budget":
        flags[1] = "3"
    elif case == "missing_cli_budget":
        flags = []
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--live", "-m", "live", *flags],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert diagnostic in result.stdout + result.stderr
