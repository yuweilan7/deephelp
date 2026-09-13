# DeepHelp 模块规划规则

本文件由 `deephelp-module-planner` Skill 在每次规划时读取。它只规定稳定的决策与交付格式；项目事实以当前仓库为准。

## 1. 输入与读取范围

当前模块以用户指定的 Mxx 为准；否则只在 `docs/MODULES/Mxx_*.md` 中解析唯一候选。开始前记录分支、HEAD 与未提交差异，并依次读取：

1. `AGENTS.md`、`docs/PROJECT_STATE.md`、`docs/CONTRACTS.md`
2. 当前模块规格及其“前置”列出的直接 handoff
3. 将被复用或修改的实际代码、测试、包配置和必要锁文件
4. 仅在相关时读取 `ARCHITECTURE / DECISIONS / ACCEPTANCE`
5. 按模块“来源”页码和 `SOURCE_MAP` 核对本机 PDF；已核对且无来源争议时不重复读全文

区分原文事实、仓库现状、工程决定和待验证事项。找不到 PDF 或哈希不符时标记 `SOURCE_NOT_VERIFIED`，不要假装核对过；缺少关键前置或真实接口时不得编造可实施方案。

## 2. 先判断组织方式

| decision | 选择条件 |
|---|---|
| `DIRECT` | 范围内聚、接口明确，一次实现即可形成完整验收闭环 |
| `DECOMPOSE` | 存在两个以上真正独立、可顺序验收和回滚的里程碑，混做会显著扩大排错范围 |
| `PROBE_FIRST` | SDK、协议、持久化或资源能力未知，探针结果会改变后续实现 |
| `VERIFY_EXISTING` | 目标产物已存在，主要工作是核验和补小缺口 |

不要按固定数量拆 S。接口、实现和相关测试通常属于同一任务；没有独立验收价值就不拆。S 不再继续拆子 Prompt，`DECOMPOSE` 也不代表并行实施。另列 `readiness=READY / BLOCKED / PENDING_LIVE`，说明真实阻塞点。

## 3. 最小充分设计

规划必须覆盖与本模块实际相关的内容：

- 目标、范围外事项、直接前置和可复用接口
- 输入输出、状态/错误、正常路径与关键失败路径
- 文件级改动、确定性代码边界、依赖与迁移影响
- unit、integration、live、e2e、故障测试中适用的层级
- 验收条件、停止条件、非破坏性回滚和下一模块交接

只展开相关主题：没有持久化就不写事务论文，没有模型调用就不写 token 预算。保留模块规格中的难例，但不复制公共架构全文。命令是待执行步骤，不是已经通过的证据。

## 4. 落盘格式

每个模块只保留一个规划入口：`planning/Mxx/PLAN.md`。

- `DIRECT`：完整实施任务直接写在 `PLAN.md`，不创建 S 文件。
- `DECOMPOSE`：`PLAN.md` 记录设计、顺序与依赖；只为确需独立执行的里程碑创建最少数量的 `Sxx_*.md`。
- `PROBE_FIRST`：探针目标、判据和结果分支写入 `PLAN.md`；探针未运行前不预生成整套 S。
- `VERIFY_EXISTING`：在 `PLAN.md` 建“要求—现有产物—检查—缺口”表，只补真实缺口。

已有规划时原位差量更新，不创建 `v2/final/new` 副本。规划阶段不实施业务代码，不创建空报告或未运行的 handoff，不自动 commit/push。完成后报告 decision、readiness、生成/修改文件、关键未定项和建议的首个执行指令。
