import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


def test_importing_modules_does_not_create_clients_load_settings_or_launch_workers():
    code = """
from unittest.mock import patch
with (
    patch('socket.socket.connect', side_effect=AssertionError('network on import')),
    patch('httpx.AsyncClient', side_effect=AssertionError('client on import')) as client,
    patch(
        'concurrent.futures.ProcessPoolExecutor',
        side_effect=AssertionError('pool on import')
    ) as pool,
):
    import deephelp_app.settings
    with patch.object(
        deephelp_app.settings.Settings, 'from_env',
        side_effect=AssertionError('env on import')
    ):
        import deephelp_app.app
        import deephelp_app.experiments
        import deephelp_app.fakes
        import deephelp_app.trace
    assert not client.called
    assert not pool.called
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
