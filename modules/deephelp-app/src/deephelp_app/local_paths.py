"""Local control/evidence paths shared by business commands and explicit tooling."""

from pathlib import Path

from deephelp_app.errors import ConfigurationError


def local_path(value: str) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(Path(".local").resolve()):
        raise ConfigurationError("Local control files and evidence must stay under .local")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
