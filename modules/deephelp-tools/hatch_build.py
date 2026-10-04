"""Include the single repository datasets in both direct and sdist-derived wheels."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ResourceHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        for packaged, repository, group in (
            ("datasets", "datasets", "evaluation"),
            ("demo-data", "demo/data", "demo"),
        ):
            source = root / packaged
            if not source.is_dir():
                source = root.parents[1] / repository
            if not source.is_dir():
                raise FileNotFoundError(f"Missing {group} resource source")
            build_data["force_include"][str(source)] = f"deephelp_tools/resources/{group}"
