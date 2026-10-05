"""Skill本地管理/离线候选试运行入口；完整发布复用原release_prepare。

draft校验机器闭包，review明确版本与身份，trial使用同核心/真实本机stdio固定资料。
离线trial不能冒充真实模型或PG发布内容证据；不安装外部Skill或执行Markdown代码。
"""

import argparse
import asyncio
import json
from pathlib import Path

from deephelp_app.adapters.releases.skill_store import SkillStore, skill_diff
from deephelp_app.application.skills import load_skills
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_tools.learning.course.offline import offline_only
from deephelp_tools.learning.course.skills import skills_demo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["details", "draft", "review", "trial"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--candidate")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--reason")
    parser.add_argument("--confirmed", action="store_true")
    args = parser.parse_args()
    registry = load_skills(args.registry)
    store = SkillStore(args.root) if args.root else None
    if args.action == "details":
        result = {
            "registry": registry.content(),
            "digest": registry.digest,
            "candidates": store.list() if store else [],
            "model_calls": 0,
        }
    elif args.action == "draft":
        if not store or not args.registry:
            parser.error("draft requires root and a new machine registry file")
        result = store.propose(registry, load_skills())
        result["changes"] = skill_diff(load_skills(), registry)
    elif args.action == "review":
        if (
            not store
            or not args.candidate
            or not args.revision
            or not args.identity
            or not args.reason
            or not args.confirmed
        ):
            parser.error("review requires root/candidate/revision/identity/reason/confirmed")
        identity = VerifiedIdentity.model_validate_json(args.identity.read_text(encoding="utf-8"))
        result = store.review(args.candidate, args.revision, identity, args.reason, True)
    else:

        async def trial() -> dict[str, object]:
            # 建loop后封业务外网；stdio子进程使用固定合成资料与原机器守卫。
            with offline_only():
                async with asyncio.timeout(30):
                    return await skills_demo(registry)

        result = asyncio.run(trial())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
