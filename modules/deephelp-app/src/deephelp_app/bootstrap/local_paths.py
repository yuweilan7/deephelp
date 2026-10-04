"""Local control/evidence paths shared by business commands and explicit tooling."""

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
