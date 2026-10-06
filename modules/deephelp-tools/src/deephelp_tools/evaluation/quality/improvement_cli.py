"""审核先于修订的显式CLI，0模型；PG保存review→Prompt→采用/拒绝。

review仅读dev；revise才写原m29 PromptArtifact与同文本实验输入；close只读已冻结holdout报告。
本命令拥有原PG池并finally释放，调用模型另沿paired_cli同一累计预算。
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from deephelp_app.adapters.persistence.postgres import PostgresConfig, PostgresPool
from deephelp_app.adapters.releases.asset_integrity import file_digest
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_tools.evaluation.quality.improvement import Proposal, close, review, revise
from deephelp_tools.evaluation.quality.improvement_store import ImprovementStore
from deephelp_tools.evaluation.retrieval.corpus_candidate import immutable


async def run(args: argparse.Namespace) -> dict[str, Any]:
    """同步私有原件与PG短事务；重投读原结果，不能撤销已经消费的holdout。"""
    owner = VerifiedIdentity(tenant_id=args.tenant, user_id=args.user)
    pool = await PostgresPool.open(PostgresConfig.read(args.postgres))
    try:
        store = ImprovementStore(pool)
        await store.migrate()
        if args.action == "review":
            proposal = Proposal.model_validate_json(
                await asyncio.to_thread(args.proposal.read_text, encoding="utf-8")
            )
            value = await asyncio.to_thread(
                review, args.dev_report, proposal, owner.model_dump(mode="json")
            )
            immutable(args.output, json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
            row = await store.create(owner, value)
        else:
            value = json.loads(await asyncio.to_thread(args.review.read_text, encoding="utf-8"))
            row = await store.get(owner, value["review_hash"])
            if row["review"] != value:
                raise ValueError("Review file differs from original PG revision")
            if args.action == "revise":
                candidate = await asyncio.to_thread(revise, value)
                immutable(args.output, candidate.model_dump_json().encode())
                immutable(args.prompt_text, candidate.text.encode())
                payload = dict(
                    request_digest=candidate.request_digest,
                    path=str(args.output.resolve()),
                    sha256=file_digest(args.output),
                    review_hash=value["review_hash"],
                )
            else:
                payload = await asyncio.to_thread(
                    close, value, args.prompt, args.holdout_report, args.contract
                )
                immutable(
                    args.output, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
                )
            row = await store.advance(owner, value["review_hash"], row["revision"], payload)
        return dict(
            review_hash=row["review_hash"],
            state=row["state"],
            revision=row["revision"],
            model_calls=0,
            failed_criteria=(row["closure"] or {}).get("failed_criteria"),
        )
    finally:
        await pool.aclose()


def main() -> None:
    p = argparse.ArgumentParser(description="M45 reviewed single improvement; no models")
    p.add_argument("action", choices=("review", "revise", "close"))
    for name in ("postgres", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    for name in (
        "dev-report",
        "proposal",
        "review",
        "prompt-text",
        "prompt",
        "holdout-report",
        "contract",
    ):
        p.add_argument("--" + name, type=Path)
    p.add_argument("--tenant", required=True)
    p.add_argument("--user", required=True)
    a = p.parse_args()
    needed = {
        "review": (a.dev_report, a.proposal),
        "revise": (a.review, a.prompt_text),
        "close": (a.review, a.prompt, a.holdout_report, a.contract),
    }
    if not all(needed[a.action]):
        p.error("Missing phase-specific evidence path")
    print(json.dumps(asyncio.run(run(a)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
