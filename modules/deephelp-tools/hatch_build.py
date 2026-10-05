"""从唯一资料源打包评测/demo资源，直接构建与sdist构建使用相同目标路径。"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ResourceHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        for packaged, repository, group in (
            ("datasets", "datasets", "evaluation"),
            ("demo-data", "demo/data", "demo"),
        ):
            # sdist已携带这两个目录；直接从工作区构建才回到仓库的原资料目录。
            # force_include只改变包内位置，不复制出另一份可独立维护的源码资料。
            source = root / packaged
            if not source.is_dir():
                source = root.parents[1] / repository
            if not source.is_dir():
                raise FileNotFoundError(f"Missing {group} resource source")
            build_data["force_include"][str(source)] = f"deephelp_tools/resources/{group}"
