"""显式读取通用中间件连接配置，导入模块不读取凭据或建立连接。

root指定项目配置位置，DEEPHELP_SECRET_FILE可选定受保护密钥文件，后者覆盖同名公开值。
只沿用原逐行KEY=VALUE解析，不执行脚本、不回显配置；Redis、Milvus和旧源迁移共用。
"""

import os
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def local_connection(root: Path) -> dict[str, str]:
    values = read_env(root / "infra/config/connections.env")
    secret = Path(
        os.environ.get("DEEPHELP_SECRET_FILE", str(Path.home() / ".deephelp/.env.secret"))
    )
    values.update(read_env(secret))
    return values
