"""显式可选学习扩展安装，生产app不反向导入tools。

install组合原协议、分析和评测router，读页面不自动启动模型/实验；身份仍沿原入口。
"""

import os
from pathlib import Path

from fastapi import FastAPI

from deephelp_app.bootstrap.commands.mvp_cli import main
from deephelp_app.bootstrap.runtime.assembly import LiveAssembly
from deephelp_tools.learning.workbench.analysis import analysis_router
from deephelp_tools.learning.workbench.evaluation import EvaluationCatalog, evaluation_router
from deephelp_tools.learning.workbench.protocol import learning_router
from deephelp_tools.learning.workbench.skills import skills_router
from deephelp_tools.learning.workbench.sop_authoring import sop_authoring_router


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
    app.include_router(analysis_router(assembly, app.state.browser_auth))
    if catalog := os.getenv("DEEPHELP_LEARNING_CATALOG"):
        config = EvaluationCatalog.model_validate_json(Path(catalog).read_text(encoding="utf-8"))
        app.include_router(
            sop_authoring_router(
                assembly,
                app.state.browser_auth,
                config.owner,
                config.task_root / "sop",
                lambda: app.state.budget_factory(),
                lambda: callable(getattr(app.state, "budget_factory", None)),
            )
        )
        app.include_router(
            skills_router(
                assembly, app.state.browser_auth, config.owner, config.task_root / "skills"
            )
        )
        app.include_router(
            evaluation_router(
                assembly,
                app.state.browser_auth,
                config,
                lambda: app.state.budget_factory(),
            )
        )


if __name__ == "__main__":
    raise SystemExit(main(configure_app=install))
