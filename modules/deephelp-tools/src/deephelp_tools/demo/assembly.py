"""Explicit optional demo composition, sharing the runtime client and engine."""

import sys
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters

from deephelp_app.adapters.tool_gateway import ToolGateway
from deephelp_app.bootstrap.mvp_runtime import LiveAssembly
from deephelp_tools.assets import asset_path
from deephelp_tools.demo.tool_config import MockConfig


def demo_server(config: MockConfig | None = None) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "deephelp_tools.demo.mcp_server"],
        env={"DEEPHELP_MCP_CONFIG": (config or MockConfig()).model_dump_json()},
    )


class DemoToolGateway(ToolGateway):
    def __init__(self, *, config: MockConfig | None = None, **kwargs: Any) -> None:
        self.config = config or MockConfig()
        super().__init__(
            server=demo_server(self.config), max_invocations=self.config.max_invocations, **kwargs
        )


class DemoAssembly(LiveAssembly):
    def __init__(
        self,
        root: Path,
        providers: Path,
        pointer: Path,
        *,
        mock: MockConfig | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("tool_server", demo_server(mock))
        kwargs.setdefault("corpus_path", asset_path("m09_corpus.jsonl"))
        super().__init__(root, providers, pointer, **kwargs)
