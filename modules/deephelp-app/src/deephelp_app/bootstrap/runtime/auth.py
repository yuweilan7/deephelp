"""把显式本地身份映射或Bearer转换为唯一VerifiedIdentity。

personal必须唯一匹配既有身份，不从正文、session或trace推断用户。
这里只选择可信主体，后续仓储仍校验对象归属；错误只暴露静态字段说明。
"""

import hmac
from pathlib import Path

from fastapi import Request

from deephelp_app.application.dense import load_json
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity


class LocalAuth:
    def __init__(self, path: Path) -> None:
        rows = load_json(path).get("tokens")
        if not isinstance(rows, list) or not rows:
            raise ConfigurationError("Configure local bearer tokens mapped to synthetic identities")
        self.rows: list[tuple[str, VerifiedIdentity]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("token"), str):
                raise ConfigurationError("Invalid local authentication mapping")
            if len(row["token"]) < 32 or not row["token"].isascii():
                raise ConfigurationError("Use a random ASCII local token of at least 32 characters")
            self.rows.append((row["token"], VerifiedIdentity.model_validate(row.get("identity"))))
        if len({token for token, _ in self.rows}) != len(self.rows):
            raise ConfigurationError("Duplicate local bearer token")

    def personal(self, path: Path | None) -> VerifiedIdentity:
        """Select an existing owner explicitly, never the first fixture/token row."""
        if path is None or not path.is_file():
            raise ConfigurationError(
                "工作台需要 --personal-identity JSON，包含现有 tenant_id 和 user_id。"
            )
        from pydantic import ValidationError

        try:
            identity = VerifiedIdentity.model_validate(load_json(path))
        except (AppError, ValidationError, ValueError) as exc:
            raise ConfigurationError("个人身份配置须仅包含有效的 tenant_id 和 user_id。") from exc
        if sum(owner == identity for _, owner in self.rows) != 1:
            raise ConfigurationError(
                "个人身份必须唯一匹配 --auth 中一条现有身份，不能选择歧义映射。"
            )
        return identity

    def __call__(self, request: Request) -> VerifiedIdentity:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token.isascii():
            token = ""
        for expected, identity in self.rows:
            if hmac.compare_digest(token, expected):
                return identity
        raise AppError(ErrorCode.UNAUTHENTICATED, "本机访问令牌无效。", 401)
