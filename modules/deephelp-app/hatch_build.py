"""源码学习不要求构建网页；正式安装包继续绑定完整前端产物。"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendHook(BuildHookInterface):
    def initialize(self, version, build_data):
        frontend = Path(self.root) / "frontend" / "built"
        if frontend.is_dir():
            build_data["force_include"][str(frontend)] = "deephelp_app/frontend"
        elif version != "editable":
            # uv sync安装源码引用，course只用Python/包内合成资料，无需Node。
            # wheel是可部署资产，不能把没有网页的包当成完整工作台发布。
            raise FileNotFoundError("正式wheel缺少frontend/built，请先运行npm ci和npm run build")
