"""Explicit optional browser learning extension; production app never imports tools."""

import os
from pathlib import Path

from fastapi import FastAPI

from deephelp_app.bootstrap.mvp_cli import main
from deephelp_app.bootstrap.mvp_runtime import LiveAssembly
from deephelp_tools.learning.workbench_protocol import learning_router


def install(app: FastAPI, assembly: LiveAssembly) -> None:
    port = os.getenv("DEEPHELP_LEARNING_RIGHTS_PORT")
    key = os.getenv("DEEPHELP_LEARNING_RIGHTS_KEY")
    if bool(port) != bool(key):
        raise ValueError("Learning demo requires both loopback port and local signing key")
    app.include_router(
        learning_router(
            assembly,
            app.state.browser_auth,
            rights_port=int(port) if port else None,
            rights_key=Path(key).read_bytes() if key else None,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main(configure_app=install))
