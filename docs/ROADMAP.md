# DeepHelp 路线图与实施流程

2026-10-02 核对了 docs 下全部 31 份 Markdown、来源清单和本机 78 页 PDF（逐页概览，架构/流水线/事件聚合/分期等关键页另做单页核对）。PDF 的 SHA-256 与 source-manifest 一致。本文负责能力顺序和工作方式；接口细节仍以 CONTRACTS 为准，实际进度仍以 PROJECT_STATE 为准。

## 复刻范围与检查结论

路线能支撑 **核心机制的等价复刻**，不能从截图恢复作者全部源码、内部业务接口、完整语料或线上指标。原文 p73–74 本身采用“先最小闭环，再核心深化，再生产联调”的顺序；本项目照这个顺序推进。

原方案的问题主要是范围混在一起、未来协议冻结过早、重复要求太多，以及可选审批成为调试/评测的前置。保留 M 编号以免破坏已有引用，但把验收分成：

- **M08 最小闭环**：3 类客诉，真实千问 + 真实 Milvus + 真实 MCP 协议，业务数据合成。
- **核心复刻**：继续完成 Hybrid、记忆、事件聚合、完整级联、FastText、SOP 配置、调试、评测和最小审核回流。
- **独立加固/扩展**：M15 持久审批、M19 部署故障演练、M20 物流迁移、M21 框架对照。启用业务写工具时 M15 必须完成，保持只读时可以后移。

基础身份隔离、请求幂等、真实调用预算和事实证据仍是核心工程要求。复杂审批、跨重启副作用恢复和生产运行体系不再压到每个早期模块上。

## PDF 到模块的对应

| 原文机制（文件页） | 对应模块 | 保留方式与工程替代 |
|---|---|---|
| 系统分层、模型适配（15–18） | M01、M03、M08 | 单应用内部按职责分层；Spring 改 Python/FastAPI/LangGraph，企业配置平台改文件配置 |
| 清洗、实体提取（19–24） | M04 | Regex + 有预算的 API 提取；本地 7B 不是默认依赖；保留否定、尾部订单和实体出处 |
| 规则→检索→记忆增强→兜底（25–35） | M02、M05、M09、M12、M13 | 注册表和每层决策可检查；不复制未知阈值或矛盾输出 |
| Dense + BM25 + 融合（35–41） | M05、M09 | 真实 Milvus 能力；API embedding/FP32 是明确替代，不能称 BGE-M3 同一空间 |
| FastText 训练/推理（41–52） | M13 | Python binding 替代 JNI；真实小模型产物和独立样本结果，不承诺作者体积/精度 |
| 数据反馈（53–54） | M17、M18 | 样本采集→审核→更新→回归；高分只进候选，不能自动变正确标签 |
| 多轮事件聚合、13 段（55–61） | M10–M12 | 保留主诉/补充判断、相似候选、归属裁决、簇约束、汇总；主意图只由 600 统一调用 |
| 短期/长期记忆、问题生命周期（62–66） | M08、M10 | Redis 替代 JIMDB，MySQL 保存业务事实，Milvus 保存可重建投影 |
| SOP、MCP、ReAct、输出（67–69） | M06–M08、M14、M16 | 真实协议 + 合成下游；有界工具循环与可配置分支；M15 是额外持久审批设计 |
| 调试、评测、分期开发（70–76） | M08、M16–M18 | 简单调试入口和可复跑评测；15+情境、500+样本是完整覆盖目标，分批扩展 |
| 企业后台、京 ME、内部工具、多供应商全覆盖 | 默认不复刻 | 原件不足以实现真实企业集成；可配置 SOP/工具适配保持扩展入口 |

图中 Rerank、多模型、本地模型及多 Agent 选项不逐个建设成服务。它们不是原文 p73–74 的首个闭环条件；需要独立复刻这些选项时再明确目标和对照实验。这样能复刻主机制，但不能称企业系统逐项原样复制。

## 每个 M 做成什么

| M | 定位 | 可见成果和退出条件 |
|---|---|---|
| M00 | 已完成设计 | 架构/接口/来源边界有依据；无需重新拆分 |
| M01 | 已完成骨架 | API、生命周期、超时取消和离线测试；不代表业务已实现 |
| M02 | 基础 | 发布当前闭环需要的唯一类型、3–5类意图和30–60条固定样本；不实现未来审批/数据库 |
| M03 | 核心 | 千问 chat/schema/tool/embed 分别实测，预算计数和失败可诊断 |
| M04 | 核心 | 长文尾订单、否定、前导零、更正不丢；不足实体返回未知 |
| M05 | 核心 | 小语料真实写入/检索 Milvus；重导不倍增、签名不混、查询不泄漏测试集 |
| M06 | 核心 | 本地 MCP 实际 list_tools/call_tool；两个只读合成工具，权限和取消可验 |
| M07 | 核心 | 三份 SOP 真正驱动工具与分支；正常/缺资料/工具失败都有结果 |
| M08 | 首个闭环 | 一条完整客诉能跑到事实回复；三类场景、账本幂等、完整 trace；真实依赖通过 |
| M09 | 核心 | 同一 query 可比较 Dense、BM25、Hybrid；不预设混合必胜 |
| M10 | 核心 | 多个开放问题、Redis 窗口、MySQL 生命周期、Milvus 投影；丢缓存不丢事实 |
| M11 | 核心 | “券不能用→另外问订单B→补券/订单A”回到原券问题；歧义澄清、不串单 |
| M12 | 核心 | 在 M08 同一流程接好聚合与四级决策；主服务只进一次，缺槽位不调用业务工具 |
| M13 | 核心 | 真实 FastText 小样本训练/推理/量化对照；兼容性失败停下，不伪造训练结果 |
| M14 | 核心 | 新 SOP 配置可校验、固定版本并产生不同分支；15+情境分批扩展，不凑重复文件 |
| M15 | 独立加固 | 只在选择持久审批/业务写工具时启动；批准→重启→对账不重复效果 |
| M16 | 核心 | 意图/多轮/流程三种信息可查看，回复事实可核对；只读版不依赖 M15 |
| M17 | 核心 + 规模扩展 | 先跑当前独立样本和增量对照，输出真实指标；完整500+覆盖另列未完成目标 |
| M18 | 核心 + 发布加固 | 先演示采集→审核→三路更新→回归/回退；自动调度/复杂发布不阻塞机制演示 |
| M19 | 独立加固 | 在授权隔离目标做容量、备份恢复和故障测试；不是本机核心演示前置 |
| M20 | 可选迁移 | 新增物流配置/适配，原客诉回归继续通过 |
| M21 | 可选对照 | 同任务/预算比较 SOP 执行器；不改主路线 |

默认顺序：M02 → M03 → M04 → M05 → M06 → M07 → M08；然后 M09 → M10 → M11 → M12 → M13 → M14 → M16 → M17 → M18。M15/M19 另选，M20/M21 按需。每个 M 可能有多个特性，依赖满足后仍一次只实现一个。

核心验收先证明各机制真正工作。15+情境和500+评测达标时才能说完整规模验收完成；小样本阶段只能报告实际覆盖和局限。

## 分支和交付流程

从 M02 起使用以下顺序。示例分支名仅为示例，本次路线审查不启动 M02。

```powershell
git status --short --branch
git switch main
git fetch origin
git pull --ff-only origin main
git switch -c feature/m02-contracts
```

工作区必须干净；已有用户改动先识别和保留，不自动 stash/丢弃。main 无法快进时先分析分歧。PLAN 只列这次特性的改动和验收，源代码、相关测试、必要文档一起交付。

本机使用已验证的 `py -3.11 -m uv`（0.12.13），应用 Python 仍为3.14.7；PATH 中裸 `uv` 当前为0.12.10，不满足根工程要求。其他机器先核对版本。离线回归使用仓库已有命令：

```powershell
py -3.11 -m uv run --locked ruff check conftest.py modules/deephelp-app
py -3.11 -m uv run --locked ruff format --check conftest.py modules/deephelp-app
py -3.11 -m uv run --locked mypy
py -3.11 -m uv run --locked pytest
git diff --check
```

特性依赖真实服务时还必须通过该特性的真实探针/路径。不存在通用的“--live 一过就全通过”：当前 M01 的 live 测试仅检查配置，M03 才会实现真实模型测试。没有真实依赖的文档/类型改动不强制联网。

验收通过后核对差异，只用 `git add -- <明确文件>` 暂存本特性文件；占位参数须替换为实际路径，不使用全仓库自动暂存。检查暂存差异和 `git diff --cached --check`，根据实际改动生成 `类型(范围): 中文说明` 的提交标题并 commit，必要时补充中文正文。随后推送特性分支并建立跟踪关系，再合并：

```powershell
git push -u origin feature/m02-contracts
git switch main
git pull --ff-only origin main
git merge --no-ff feature/m02-contracts -m "merge(M02): 合并共享领域类型与意图样本"
```

在合并后的 main 上重跑上述检查；如果远端 main 已变化，先审查新增变化，再重验受影响真实路径。通过后：

```powershell
git push origin main
git switch feature/m02-contracts
git merge --ff-only main
git push origin feature/m02-contracts
git fetch origin
git rev-parse main feature/m02-contracts origin/main origin/feature/m02-contracts
git ls-remote origin refs/heads/main refs/heads/feature/m02-contracts
git switch main
git status --short --branch
```

逐条检查退出码，任一步失败不执行后续步骤。最终本地 main/特性分支、对应远端跟踪分支及两个实际远端分支 SHA 必须全部一致，工作区必须干净。使用中文 merge commit 保留特性边界；随后将特性分支快进到该提交，不强推。保留已完成 feature 分支，最后回到 main；下一特性同步 main 后另建分支。纯讨论、只读审查和纯规划不自动 commit/push；正常特性及用户明确要求交付的文档改动，已授权自动完成 add、中文 commit、push、合并和分支同步。

## 真实环境怎样验证

| 情况 | 要做什么 | 不通过时 |
|---|---|---|
| M02 类型/fixture、纯文档改动 | 离线检查 | 修复当前改动；不消耗真实额度 |
| 模型网关、模型提取、真实 SOP | 先确认模型/目标/本地调用、token、费用硬上限；少量真实协议与业务调用 | 无额度/鉴权/能力/预算问题立即停并报告 |
| Milvus/Redis/MySQL 特性 | 先只读检查当前连接，再在专用表/前缀/集合验证实际操作 | 不可达立即停；不重建 P00、不切换成本机替代 |
| 协议 Mock 与故障分支 | 真实 MCP 协议，合成业务和离线失败注入 | 明确测试的层级，不当作真实模型/公司下游成功 |
| 服务重启、删除、恢复、崩溃演练 | 事先明确授权、隔离目标、停止/清理边界 | 不在现有共享云服务盲目执行 |

报告至少写失败组件、脱敏错误/请求 ID、真实尝试次数和已用额度（未知就写未知）、已改文件、恢复所需事项。实时故障与离线注入要区分：离线测试可证明错误处理正确，但实时依赖故障仍阻止该特性合并和后续推进。

## 文档保留规则

| 文档 | 用途 | 每次要不要读/新增 |
|---|---|---|
| README、ROADMAP、PROJECT_STATE | 入口、路线、真实进度 | 从这里开始；状态只记证据 |
| AGENTS、PLAN_MODULE_PROMPT | Agent 的固定工作规则和短计划格式 | 保留一份，不要求用户学内部术语 |
| MODULES/Mxx | 能力规格 | 只读当前 M；不随便重编号 |
| ARCHITECTURE、CONTRACTS | 架构和共享接口 | 按改动需要查；不复制到每份计划 |
| DECISIONS、SOURCE_MAP、来源清单 | 为什么这样改、PDF 对照 | 只在裁决/来源核对时更新 |
| ACCEPTANCE、RISKS | 跨模块难例和真实风险 | 按特性取用；不再额外建同内容的清单 |
| M01_ASYNC_GUIDE | 已有学习材料 | 保留，不作为每次必读项 |
| handoffs/P00、M00、M01及未来Mxx | 已完成事实、命令和下游接口 | 现有只有3份，保留；每个M最多一份，增量更新 |
| planning/Mxx/PLAN.md | 当前实施计划 | 每个M最多一份；不新增S、Prompt套娃、空handoff或重复日报 |

已有交接含历史环境和实际命令，删除会损失证据；它们不是全部注入每次会话的材料。本轮只新增本路线入口，收缩原有入口和计划规则，不增加新的文档体系。

## 本次文档修订的验证记录（2026-10-02）

初始main工作区干净，基线commit为 `5fda169`；本轮改动在 `feature/docs-roadmap-workflow`，未启动M02业务实施。只修改文档和新增本路线入口，不修改源码/测试、根锁、依赖、云端数据或既有handoff。

| 实际命令 | 退出码 | 结果 |
|---|---:|---|
| `pdfinfo docs/客诉场景的自动驾驶DeepHelp.pdf`、PDF SHA-256核对 | 0 | 78页；哈希匹配来源清单 |
| `uv run --locked python --version`及裸uv检查命令 | 1 | PATH中uv0.12.10被项目要求拒绝，未运行对应检查；改用已存在的合格启动器 |
| `py -3.11 -m uv --version` | 0 | uv0.12.13；未更新全局工具 |
| `py -3.11 -m uv run --locked ruff check conftest.py modules/deephelp-app` | 0 | All checks passed |
| `py -3.11 -m uv run --locked ruff format --check conftest.py modules/deephelp-app` | 0 | 24 files already formatted |
| `py -3.11 -m uv run --locked mypy` | 0 | 11个源文件通过 |
| `py -3.11 -m uv run --locked pytest` | 0 | Python3.14.7，48 passed / 1 skipped；253条既有pytest-asyncio弃用警告 |
| PowerShell内嵌Python文档检查：`$routeAudit \| py -3.11 -X utf8 -c "import sys; exec(sys.stdin.read().lstrip(chr(65279)))"` | 0 | 35份Markdown的相对链接/代码围栏通过；核心前置顺序、21份改动仅文档、M02未启动、PDF哈希不变 |
| 首次文档检查直接以 `py -3.11 -X utf8 -` 读取PowerShell管道 | 1 | 管道带BOM导致语法错误；去除BOM后重跑通过，无业务代码改动 |
| `git diff --check` | 0 | 无空白错误 |
| `git check-ignore`核对PDF与本次临时页图 | 0 | 两者均被Git忽略 |

初次路线审查阶段未执行：真实千问调用、远程中间件连接/修改、GitHub Actions、M02业务实现、commit/merge/push；当时保留文档差异供查看。后续用户明确要求用当前 `feature/docs-roadmap-workflow` 实际验证 Git 交付流程，按上面的顺序推进；实际 Git 命令、退出码和最终远端 SHA 在交付回复中报告，不将待执行步骤记作通过。

受影响路径：根 `AGENTS.md`/`README.md`；应用 `modules/deephelp-app/README.md`；docs 的 `ROADMAP / PROJECT_STATE / PLAN_MODULE_PROMPT / ARCHITECTURE / CONTRACTS / DECISIONS / ACCEPTANCE / RISKS`；MODULES 的 `M02 / M03 / M05 / M14 / M15 / M16 / M17 / M18 / M19 / M20`。既有 `P00/M00/M01` handoff保持原历史记录。

清理限制：本轮临时页图位于被Git忽略的 `.local/route-audit-20261002/`。递归清理命令被自动审批策略拒绝（`blocked by policy`，命令未执行，无进程退出码）；约67.4MiB临时文件保留，原PDF不变。
