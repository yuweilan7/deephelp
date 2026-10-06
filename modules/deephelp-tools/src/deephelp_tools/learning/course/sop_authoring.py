"""离线作者实验复用正式草稿事实源与原图校验，不复制业务执行器。"""

from pathlib import Path
from tempfile import TemporaryDirectory

from deephelp_app.adapters.releases.sop_drafts import SOPDraftStore
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import VerifiedIdentity


async def sop_authoring_demo() -> dict[str, object]:
    """演示悬空边定位、修正与旧revision拒绝；Temp由实验拥有并自动释放。"""
    base = bundled_registry()
    owner = VerifiedIdentity(tenant_id="course", user_id="reader")
    with TemporaryDirectory(prefix="deephelp-sop-author-") as directory:
        store = SOPDraftStore(Path(directory))
        draft = store.create(owner, base, base.definitions[0].sop_id)
        content = draft["content"]
        content["nodes"][0]["success"] = "missing"
        invalid = store.update(draft["draft_id"], owner, 1, content)
        content["nodes"][0]["success"] = "decide"
        content["version"] = "course-author-v2"
        saved = store.update(draft["draft_id"], owner, 2, content)
        try:
            store.update(draft["draft_id"], owner, 2, content)
        except AppError as exc:
            conflict = exc.code.value
        else:
            raise AssertionError("旧revision必须由真实草稿存储拒绝，不能报告CAS演示成功。")
        return {
            "invalid_graph": invalid["validation"],
            "saved_revision": saved["revision"],
            "reloaded_equal": store.get(draft["draft_id"], owner) == saved,
            "stale_revision": conflict,
            "published_unchanged": base.snapshot_hash == draft["base_hash"],
            "model_calls": 0,
        }
