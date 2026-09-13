# DeepHelp

复刻《客诉场景的自动驾驶 DeepHelp》中可迁移、可验证的核心机制，并补齐权限、幂等、恢复和评测等生产边界。项目当前只有基础设施与设计基线，业务代码从 M01 开始实现。

## 当前入口

- [AGENTS.md](AGENTS.md)：每次本地实施会话必须遵守的工程规则
- [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md)：唯一进度事实
- [docs/PLAN_MODULE_PROMPT.md](docs/PLAN_MODULE_PROMPT.md)：模块规划与是否拆分的判定规则
- [docs/MODULES](docs/MODULES)：M00-M21 共 22 份模块专属规格
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)、[docs/CONTRACTS.md](docs/CONTRACTS.md)：架构与跨模块语义
- [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)：跨模块场景和运行验收基线
- [handoffs](handoffs)：已完成阶段向直接后继交接的事实

`planning/Mxx/` 只在规划某个模块时创建：始终以一份 `PLAN.md` 为入口，确有独立验收边界时才增加 `Sxx_*.md`。它不是第二套长期知识库。

## 推荐工作方式

1. 在 ChatGPT 客户端打开仓库，调用本地 `deephelp-module-planner` Skill 并指定当前模块，例如“规划 M01”。
2. Skill 从仓库读取当前状态、模块规格、直接前置交接、相关代码和 PDF 对应页，判断 `DIRECT / DECOMPOSE / PROBE_FIRST / VERIFY_EXISTING`。
3. 用 Astra 极高在一个主会话中按 `planning/Mxx/PLAN.md` 顺序实施和测试；已有 S 时逐个完成，不再递归规划。
4. 模块完成后更新 `handoffs/Mxx.md` 与 `docs/PROJECT_STATE.md`。复杂模块可针对固定 commit 做一次独立只读审查。

原 PDF 放在本机 `docs/` 下即可，`docs/*.pdf` 已被 Git 忽略；身份与页码索引见 [docs/SOURCE_MAP.md](docs/SOURCE_MAP.md)。不要把原 PDF、内部截图、真实客户标识或凭据提交到公共仓库。

## 工程基线

- Python 3.12，根目录一个 uv workspace 和一个 `uv.lock`
- 首个业务包由 M01 在 `modules/deephelp-app/` 创建
- MySQL 保存业务事实，Redis 是可重建缓存，Milvus 是可重建检索投影
- 主路线为 FastAPI/Pydantic/LangGraph；AgentScope 仅作为 M21 可选对照
- 22 个开发阶段不等于 22 个 Python 包、服务或会话

```powershell
cd D:\IdeaProject\deephelp
uv sync --locked --all-packages
uv run python --version
```

已有 `infra/` 是 P00 历史基础设施，不因开始 M01 而重建。当前真实进度和未验证门禁以 `docs/PROJECT_STATE.md` 为准。
