"""Static regressions for prompt policy; not business/runtime correctness tests."""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class PromptPolicyTests(unittest.TestCase):
    def test_all_22_modules_have_adaptive_policy_and_specific_boundaries(self):
        files = sorted((ROOT / "planning/modules").glob("M*.md"))
        self.assertEqual(len(files), 22)
        for path in files:
            with self.subTest(module=path.name):
                content = path.read_text(encoding="utf-8")
                for token in ("DIRECT", "DECOMPOSE", "PROBE_FIRST", "VERIFY_EXISTING",
                              "## 本模块目标", "## 接口与分期边界", "## 本轮交付"):
                    self.assertIn(token, content)
                self.assertNotRegex(content, r"(?:拆成|给详细设计、)\s*3[—–-]7")

    def test_manifest_ids_and_dependencies_remain_explicit(self):
        value = json.loads(read("planning/manifest.json"))
        self.assertEqual([x["id"] for x in value["modules"]], [f"M{i:02d}" for i in range(22)])
        self.assertTrue(value["modules"][21]["optional"])

    def test_planner_does_not_require_a_second_layer(self):
        content = read("planning/PLAN_MODULE_PROMPT.md")
        for token in ("DIRECT", "DECOMPOSE", "PROBE_FIRST", "VERIFY_EXISTING",
                      "IMPLEMENT.md", "不预设子任务数量", "一个 M 一个主会话"):
            self.assertIn(token, content)
        self.assertIn("GitHub 不能读取未提交的 .local 文件", content)

    def test_s06_requires_coverage_not_file_existence(self):
        content = read("planning/M00/S06_ACCEPTANCE_HANDOFF.md")
        for i in range(1, 6):
            self.assertIn(f"| S{i:02d} |", content)
        self.assertIn("缺口", content)
        self.assertIn("单会话双视角", content)

    def test_waiting_slot_is_not_lost_by_active_only_filter(self):
        for rel in ("docs/CONTRACTS.md", "planning/modules/M10_MEMORY_LIFECYCLE.md",
                    "planning/modules/M11_EVENT_CLUSTER.md"):
            with self.subTest(path=rel):
                self.assertIn("WAITING_SLOT", read(rel))
                self.assertIn("WAITING_APPROVAL", read(rel))
        self.assertIn("普通有状态请求失败关闭", read("planning/modules/M11_EVENT_CLUSTER.md"))

    def test_ingest_preserves_reference_query_split(self):
        content = read("planning/modules/M05_DENSE_INGEST.md")
        self.assertIn("train/reference", content)
        self.assertIn("dev/test", content)
        self.assertIn("不得入库", content)

    def test_sync_wrapper_supports_existing_python_launcher(self):
        content = read("scripts/sync-local.ps1")
        self.assertIn("'-3.11', '-m', 'uv'", content)
        self.assertIn("--ff-only", content)
        self.assertIn("git status --porcelain", content)

    def test_context_export_includes_workflow_but_not_private_sources(self):
        content = read("scripts/project_context.py")
        core = content.split("CORE = (", 1)[1].split(")", 1)[0]
        self.assertIn("docs/WORKFLOW.md", core)
        self.assertNotIn(".local/references", core)


if __name__ == "__main__":
    unittest.main()
