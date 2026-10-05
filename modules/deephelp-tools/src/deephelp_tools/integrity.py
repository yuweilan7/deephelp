"""显式工具侧来源/字节完整性辅助，复用运行端的唯一校验能力。

只解释安装/源码来源与资源摘要，不触发模型、发布切换或数据库效果。
"""

from pathlib import Path

from deephelp_app.adapters.releases.asset_integrity import file_digest, provenance
from deephelp_app.application.corpus import digest
from deephelp_tools.assets import GROUPS, PACKAGE, asset_path


def evaluation_provenance() -> dict[str, object]:
    value = provenance()
    inputs = {"deephelp_app/" + name: sha for name, sha in value["artifact_hashes"].items()}
    inputs.update(
        {
            "deephelp_tools/" + p.relative_to(PACKAGE).as_posix(): file_digest(p)
            for p in sorted(PACKAGE.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json", ".jsonl", ".txt", ".html", ".sql"}
        }
    )
    for group, names in GROUPS.items():
        for name in sorted(names):
            inputs["deephelp_tools/" + (Path("resources") / group / name).as_posix()] = file_digest(
                asset_path(name)
            )
    value["tooling_digest"] = digest(inputs)
    value["evaluation_artifact_hashes"] = inputs
    return value
