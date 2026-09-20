# DeepHelp 本地实施约定

## 开始任务

先读 `docs/PROJECT_STATE.md`、`docs/CONTRACTS.md`、当前任务和直接前置 `handoffs/Mxx.md`，再读将要修改的代码与测试。只有涉及架构裁决、来源核对或新增跨模块约束时，才读 `docs/ARCHITECTURE.md`、`docs/DECISIONS.md`、`docs/SOURCE_MAP.md` 和 PDF 对应页。

规划当前模块时遵守 `docs/PLAN_MODULE_PROMPT.md`，模块规格唯一位于 `docs/MODULES/Mxx_*.md`。不要默认读取全部 22 份规格，也不要把聊天摘要当作仓库事实。

## 工程不变量

保留 Python 3.14.7、根 uv workspace、单一 `uv.lock` 和既有 `infra/`。首个业务包由 M01 创建在 `modules/deephelp-app/`；22 个阶段不是 22 个包或微服务。

MySQL 是业务事实源，Redis 与 Milvus 均可重建，checkpoint 不是业务账本。区分 tenant、user、session、question、message、request、run、trace、operation 与 approval。只有 600 INTENT_RECOGNIZE 进入统一主意图服务；聚合、槽位和 SOP 不重复主分类。

身份、权限、状态版本、幂等、预算、超时、审批绑定、工具白名单和结果事实必须由代码与测试约束。普通新消息不能批准或恢复旧操作；副作用结果不确定时先对账，不盲目重放。

## 实施与验证

已有 `planning/Mxx/PLAN.md` 或 `Sxx_*.md` 时直接实施，不再拆下一层 Prompt。一个 M 默认一个主写者、一个工作区、按依赖顺序推进；测试随实现完成。额外 Agent 默认只做固定 commit 的只读审查或资料核对，未经明确授权不并行写共享代码、锁、迁移或测试数据。

默认离线测试。真实模型、付费调用、云端修改和故障演练必须有明确授权、目标和预算。Mock、合成数据和文档检查不能冒充真实集成或线上效果。

不得覆盖用户未提交修改，不使用 `reset --hard`、`clean -fd`、force push，不顺便重建 P00 或实现下一模块。完成后记录实际命令、退出码、未执行项和受影响路径；更新本模块 handoff，只有模块集成人更新 `docs/PROJECT_STATE.md`。

原 PDF、截图、内部地址、真实客户编号与凭据不得提交公共仓库。本机 PDF 放 `docs/`，由 `.gitignore` 排除。
