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
    import deephelp_app.bootstrap.settings
    with patch.object(
        deephelp_app.bootstrap.settings.Settings, 'from_env',
        side_effect=AssertionError('env on import')
    ):
        import deephelp_app.api.app
        import deephelp_tools.learning.experiments
        import deephelp_tools.learning.fakes.memory
        import deephelp_app.adapters.observability.trace
        import deephelp_app.domain.checks
        import deephelp_app.domain.registry
        import deephelp_tools.evaluation.quality.samples
        import deephelp_app.application.corpus
        import deephelp_app.application.dense
        import deephelp_app.adapters.retrieval.milvus_dense
        import deephelp_tools.evaluation.retrieval.dense_cli
        import deephelp_app.adapters.mcp.business.mcp_protocol
        import deephelp_tools.demo.mcp_server
        import deephelp_app.adapters.mcp.business.tool_gateway
        import deephelp_tools.probes.protocol.mcp_smoke
        import deephelp_app.application.sop_config
        import deephelp_app.application.sop
        import deephelp_tools.learning.replay.sop_replay
        import deephelp_tools.probes.conversation.sop_probe
    assert not client.called
    assert not pool.called
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
