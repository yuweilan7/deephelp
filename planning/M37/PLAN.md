# M37 实施计划

规格：[本地核心原理教学与云端完整学习入口](../../docs/MODULES/M37_LOCAL_CORE_LEARNING.md)。
规划基线：`main` / `ae50fdf0cfd7c935d4e8e3921548deff8c7691d6`，开始时工作区干净；规划分支`feature/m37-m39-learning-finish-plan`。实施前重新核对状态，不沿用本次干净结论。
判断：`VERIFY_EXISTING`，已有course/原用例/云工作台，先验证并补真实缺口。首项可开始；完整云教学的当前可用性待实际核验。下面命令均为待执行，不是通过证据。

## 1. 做成什么

本地以有限资源运行核心Python/Agent机制，云端保留完整线上教学；不要求环境或功能完全一致。本地默认离线，不依赖既有.local/云账号/隧道，明确固定模型与内存适配边界。两个特性按顺序完成，不新增S或第二套引擎。

## 2. 从哪里改

必读AGENTS、STATE、ROADMAP、当前规格，以及[M33](../../handoffs/M33.md)、[M36](../../handoffs/M36.md)直接交接。真实云检查前读LOCAL_SETUP与受保护依赖索引。

| 顺序/拟实施分支 | 复用与允许改动 | 独立交付 |
|---|---|---|
| 1 / `feature/m37-local-core-learning` | `modules/deephelp-tools/src/deephelp_tools/learning/course/`、`learning/replay/`、其现有测试及`docs/learning/`；仅为实际阻塞缺口调整原核心/装配并说明影响 | 本地命令、结果说明与相关反例可独立验收 |
| 2 / `feature/m37-learning-entrypoints` | 根/应用/工具包README、`docs/LOCAL_SETUP.md`、课程导航；必要时调整既有workbench/启动配置入口，保持当前云能力 | 当前两种学习方式清楚，云入口与原记录有实际或适用证据 |

先核对现有`course request/memory/retrieval/coordination/cancellation/resources`及原审批测试；不因计划列多个能力就重写它们。拟新增接口尚不存在，只有确认缺口才设计。配置中秘密仍留受保护位置。

## 3. 怎样证明

环境准备沿用根锁；在不加载.env.local、不使用旧状态的场景核验入口。按规格逐项选择现有实验，首条为`py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request`；核对缺槽/补槽/原receipt/拒识关系，再选择memory/retrieval和异步资源实验。审批复用`modules/deephelp-app/tests/unit/approval/test_m15_approval.py`的UNKNOWN/幂等反例。

相关回归候选：`py -3.14 -m uv run --locked pytest modules/deephelp-tools/tests/unit/learning/test_course.py modules/deephelp-app/tests/integration/test_m12_pipeline.py --tb=short -q`；按实际更改选择其余测试、Ruff/mypy/导入/链接检查。既有实验直接满足要求时保留实现，只补入口与解释。

云端先只读当前个人入口、原事实/run/解释/观测及配置能力；没有相关行为/安装变化不发新消息、不跑全套live。确需行为或安装变化时先按ROADMAP说明影响、固定样本、预计调用/token范围、共享余量上限和无需重验项，再执行适用内容验证；不在规划中预设永久预算。

## 4. 什么时候停

两项完成才结束M37；本地资源限制不得变成全栈搭建任务。真实不可恢复阻塞、验收失败或Git问题按AGENTS停止，未验云范围按实记录。范围外功能不实施，不自动启动M38。

## 5. 怎样交付

每项按AGENTS独立分支验收、中文提交/推送/合并/六SHA同步，main不复验。实际实施才创建`handoffs/M37.md`并更新STATE，区分本地机制、云历史证据、当前观察和未执行项。回退采用新的特性分支revert；不回滚业务数据库或清零预算。全部完成后删除重复PLAN并修正链接。
