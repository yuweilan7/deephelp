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
        import deephelp_app.learning.experiments
        import deephelp_app.learning.fakes
        import deephelp_app.trace
        import deephelp_app.domain.checks
        import deephelp_app.domain.registry
        import deephelp_app.evaluation.samples
        import deephelp_app.corpus
        import deephelp_app.dense
        import deephelp_app.milvus_dense
        import deephelp_app.dense_cli
        import deephelp_app.mcp_protocol
        import deephelp_app.demo.mcp_server
        import deephelp_app.tool_gateway
        import deephelp_app.probes.mcp_smoke
        import deephelp_app.sop_config
        import deephelp_app.sop
        import deephelp_app.learning.sop_replay
        import deephelp_app.probes.sop_probe
    assert not client.called
    assert not pool.called
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
