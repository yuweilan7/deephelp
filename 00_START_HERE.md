# DeepHelp：工程入口

当前代码状态见[PROJECT_STATE](docs/PROJECT_STATE.md)。核心架构和规划已经在工程内，不要重新初始化P00，也不要把文档存在当业务实现完成。

## 最短操作路径

先用[修订后的S06](planning/M00/S06_ACCEPTANCE_HANDOFF.md)对S01–S05的既有成果做覆盖核验，需要哪项才补哪项；不要求重写五轮。通过最终设计验收后实施M01。

后续每个M用[通用规划Prompt v2](planning/PLAN_MODULE_PROMPT.md)：由Pro判断DIRECT、DECOMPOSE、PROBE_FIRST或VERIFY_EXISTING。一个M默认一个主会话按顺序完成任务，测试随实现交付；S不是必须创建的第二层。具体方法见[工作流](docs/WORKFLOW.md)。

| 工作 | 必要上下文 | 不默认读取 |
|---|---|---|
| 实施当前M/S | AGENTS、CONTRACTS、STATE、当前任务、直接前置交接、相关代码/测试 | 全22份Prompt、合订本、原PDF全文 |
| Pro设计当前M | 当前M、上述工程事实及相关PDF页 | 全部未来模块和每份历史报告 |
| 来源/架构核对 | SOURCE_MAP、DECISIONS、必要原图 | 不能访问的内部网页或推测的源码 |

## 原始资料：本地齐全，不公开上传

Windows工作区：`D:\IdeaProject\deephelp`。
原PDF：`.local/references/deephelp-original.pdf`。
原ZIP：`.local/references/deephelp-starter-original.zip`。
原解压稿：`.local/references/starter-original/`（包含合集和原始Prompt，只作历史资料）。

归位哈希与记录见 `reports/M00/windows-sync-v2.json`。下载/网盘目录原件未删除。公开仓库只有哈希、页码索引与修订版规划，没有原始PDF/ZIP/内部截图。新Pro只有GitHub访问能力时仍需上传PDF；本机文件不是远程仓库文件。

有效模块规范在[planning/modules](planning/README.md)，不要用归档中的PG/4GiB旧假设覆盖现仓库。

## 无仓库读取能力时导出上下文

```powershell
py -3.11 -m uv run --locked python scripts/project_context.py export --module M01
```

文件位于 `.local/context/M01_CONTEXT.md`，包含白名单工程文档/当前任务/前置交接，不含原PDF、凭据或完整实现代码。缺失相关代码时仍需补充，不宣称包内包含所有事实。已有uv命令也可直接替换`py -3.11 -m uv`。

文档检查：`py -3.11 -m uv run --locked python scripts/project_context.py verify`。
文档工具测试：`py -3.11 -m uv run --locked python -B -m unittest discover -s tests/docs -v`。

本轮未实施M01或后续业务模块；真实模型、云机、端到端与恢复测试保留各自待执行状态。修订清单见[逐模块复核](reports/M00/prompt-review-v2.md)。
