"""运行时解释已审核的字面规则，不执行用户或规则里的表达式。

只有approved且目录内可执行的标签可产生RuleMatch；排除词与否定先挡住误命中。
保留当前原文片段和审核版本，最终选择仍交给600，候选规则不会自动生效为审批。
"""

import re
from typing import Any

from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.feedback import RouteReview


def rule_matches(data: dict[str, Any], request: Any) -> list[Any]:
    """Data-only literal predicates, retaining original text spans and source revision."""
    from deephelp_app.application.text_entity import INJECTION, width_normalize
    from deephelp_app.domain.models.entities import EntitySource
    from deephelp_app.domain.models.intents import RuleMatch

    if data.get("format") != "m18-literal-rules-v1":
        raise ConfigurationError("Invalid reviewed rule artifact")
    if INJECTION.search(request.raw_text) or re.search(
        r"不是|已恢复|已经可以|已经能用", request.raw_text
    ):
        return []
    result = []
    for raw in data["rules"]:
        review = RouteReview.model_validate(raw["review"])
        if review.state != "approved" or not review.label or not review.label.actionable:
            raise ConfigurationError("Unapproved rule artifact")
        if not review.phrases or any(
            not 2 <= len(p) <= 80 for p in (*review.phrases, *review.exclusions)
        ):
            raise ConfigurationError("Invalid literal rule predicates")
        text = width_normalize(request.raw_text)
        if any(p in text for p in review.exclusions) or not all(p in text for p in review.phrases):
            continue
        start = min(text.index(p) for p in review.phrases)
        end = max(text.index(p) + len(p) for p in review.phrases)
        result.append(
            RuleMatch(
                rule_id="m18-" + raw["candidate_id"][:16],
                rule_version=data["version"],
                candidate_code=review.label,
                priority=5,
                start=start,
                end=end,
                evidence=EntitySource(
                    message_id=request.message_id, excerpt=request.raw_text[start:end]
                ),
            )
        )
    return result
