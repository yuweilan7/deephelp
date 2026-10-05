"""把显式控制/证据路径约束到当前state根，供业务命令和工具共用。

.local相对路径映射到state_root，resolve后再检查归属；不能用路径参数逃到任意目录。
local_path创建必要父目录但不读取凭据、不清理现有运行资产。
"""

from pathlib import Path

from deephelp_app.application.artifact_paths import state_root
from deephelp_app.domain.errors import ConfigurationError


def local_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() and path.parts and path.parts[0] == ".local":
        path = state_root().joinpath(*path.parts[1:])
    path = path.resolve()
    if not path.is_relative_to(state_root()):
        raise ConfigurationError("Control files and evidence must stay under the state root")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
