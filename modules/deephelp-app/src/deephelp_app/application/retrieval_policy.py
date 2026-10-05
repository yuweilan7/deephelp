"""启动时验证已发布检索选择，业务运行不重新读dev/test。

validate_published_selection核对scope、语料摘要、分词器和候选预算；冻结查询hash只作来源。
选参质量由发布者验，启动校验通过不代表新的模型质量通过。
"""

import re
from typing import Any

from deephelp_app.application.corpus import digest
from deephelp_app.application.hybrid import ANALYZER
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.retrieval import DenseScope, RerankPolicy


def validate_published_selection(
    value: dict[str, Any], scope: DenseScope, index_digest: str, top_k: int
) -> None:
    payload = {k: v for k, v in value.items() if k != "selection_digest"}
    if (
        value.get("format") not in {"m09-dev-selection-v1", "m31-dev-selection-v1"}
        or value.get("scope") != scope.model_dump(mode="json")
        or value.get("index_digest") != index_digest
        or any(
            not isinstance(value.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
            for key in ("dev_digest", "test_digest")
        )
        or isinstance(top_k, bool)
        or not 1 <= top_k <= 20
        or value.get("analyzer") != ANALYZER
        or value.get("selection_digest") != digest(payload)
        or isinstance(value.get("dense_weight"), bool)
        or value.get("dense_weight") not in {0.25, 0.5, 0.75}
    ):
        raise ConfigurationError("Published selection/corpus/scope/budget mismatch")
    if value["format"] == "m09-dev-selection-v1":
        if value.get("candidate_budget") != top_k or "rerank_policy" in value:
            raise ConfigurationError("Legacy selection budget mismatch")
    else:
        try:
            policy = RerankPolicy.model_validate(value["rerank_policy"])
        except ValueError, KeyError, TypeError:
            raise ConfigurationError("Invalid frozen rerank policy") from None
        if (
            value.get("top_k") != top_k
            or policy.candidate_budget < top_k
            or value.get("candidate_budget") != policy.candidate_budget
            or value.get("rerank_fingerprint") != policy.fingerprint
        ):
            raise ConfigurationError("Frozen rerank policy/budget mismatch")
