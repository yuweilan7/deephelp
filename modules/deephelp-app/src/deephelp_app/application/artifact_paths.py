"""把不可变资产引用映射到显式宿主目录，不重写签名JSON里的原路径。

跨Windows/Linux恢复必须声明唯一来源根与目标根，拒绝路径逃逸、歧义及相对绑定。
resolve_artifact只决定读取位置，消费者仍需校验原字节摘要与版本。
"""

import json
import os
from pathlib import Path, PureWindowsPath

from deephelp_app.domain.errors import ConfigurationError


def state_root() -> Path:
    value = os.getenv("DEEPHELP_STATE_ROOT")
    root = Path(value) if value else Path.cwd() / ".local"
    if not root.is_absolute():
        raise ConfigurationError("DEEPHELP_STATE_ROOT must be absolute")
    return root.resolve()


def resolve_artifact(value: str | Path) -> Path:
    """Map a declared legacy Windows root to a host directory, preserving original bytes."""
    raw = str(value)
    legacy = PureWindowsPath(raw)
    if not legacy.drive:
        return Path(raw).resolve()
    if ".." in legacy.parts or not legacy.is_absolute():
        raise ConfigurationError("Legacy artifact reference must be absolute without traversal")
    config = os.getenv("DEEPHELP_ARTIFACT_MAP")
    if not config:
        if os.name == "nt":
            return Path(raw).resolve()
        raise ConfigurationError("Windows artifact requires an explicit host binding")
    path = Path(config)
    if not path.is_absolute() or path.stat().st_size > 65536:
        raise ConfigurationError("Artifact map must be a bounded absolute configuration file")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise ConfigurationError("Artifact map requires 1..16 root bindings")
    matches = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"source", "target"}:
            raise ConfigurationError("Artifact binding requires source and target roots")
        source = PureWindowsPath(row["source"])
        target = Path(row["target"])
        if not source.is_absolute() or ".." in source.parts or not target.is_absolute():
            raise ConfigurationError("Artifact binding roots must be absolute")
        if os.name == "nt" and Path(raw).resolve().is_relative_to(target.resolve()):
            matches.append(Path(raw).resolve())
            continue
        if legacy.is_relative_to(source):
            root = target.resolve()
            resolved = root.joinpath(*legacy.relative_to(source).parts).resolve()
            if not resolved.is_relative_to(root):
                raise ConfigurationError("Artifact binding escapes its root through a link")
            matches.append(resolved)
    if len(matches) != 1:
        raise ConfigurationError("Artifact reference requires exactly one matching binding")
    return matches[0]
