# M38 实施计划

规格：[Python与Agent源码的中文讲解和职责收口](../../docs/MODULES/M38_PYTHON_AGENT_READABILITY.md)。
规划基线：`main` / `ae50fdf0cfd7c935d4e8e3921548deff8c7691d6`，开始时工作区干净；规划分支`feature/m37-m39-learning-finish-plan`。
判断：`DECOMPOSE`，按主链、支撑路径两个顺序特性交付。[M37实际交接](../../handoffs/M37.md)已具备。本轮从干净同步main `8e0a5f3`启动，主写者按两项顺序推进；云端保留M36安装。

特性1已在`feature/m38-agent-core-comments`完成主链/领域60个Python文件走查与中文讲解，实际检查见[交接](../../handoffs/M38.md)。保留Conversation原方法绑定：方法仍属于同一实例，现有类型检查/调用者明确，不为移除绑定再加委托层。下一项继续适配器、装配、tools/services、infra Python与复杂测试，并依据调用关系判断候选整理；不自动部署或启动M39。

## 1. 做成什么

系统审阅自有Python/Agent代码，需要解释的地方用通俗详细中文讲清输入输出、分支原因、状态、等待与失败边界；简单代码不凑注释。处理真正影响阅读的局部职责混杂，保持原引擎和行为。排除前端、数据JSON/JSONL、锁和生成/第三方文件的注释任务。

## 2. 从哪里改

开始时读AGENTS、STATE、ROADMAP、当前规格、届时产生的M37实际handoff，以及[M32](../../handoffs/M32.md)、[M33](../../handoffs/M33.md)。核对现有源码导航，不重复旧迁移。

| 顺序/拟实施分支 | 主要路径与复用入口 | 验收边界 |
|---|---|---|
| 1 / `feature/m38-agent-core-comments` | app的`domain/`、`application/conversation/`、event_cluster、text_entity、intent/cascade、sop、reply、approval；现有课程与核心测试 | 沿消息主线能够解释正常、补槽、重投、失败和恢复；注释与当前实现一致 |
| 2 / `feature/m38-python-support-readability` | app的adapters/bootstrap/api、tools的learning/evaluation/probes、services、自有相关infra Python及复杂测试；现有源码导航/课程 | 支撑资源、模型协议、持久化、MCP、评测发布讲清；完成全范围审阅及必要职责收口 |

初步候选：`adapters/retrieval/milvus_dense.py`中的通用配置/SSH容量，`adapters/memory_projection/redis_memory.py`的配置依赖，tools的`learning/workbench/evaluation.py`路由/任务，Conversation方法绑定。先核对消费者、M37实际改动及更直接的表达方式；不预设所有候选必须拆文件。

## 3. 怎样证明

建立临时审阅清单，最终handoff只保留子系统覆盖、代表性讲解、例外及职责变更。通过实际源码走查确认说明通俗、准确、足够深入，不以行数/比例或挑几个样板代替全范围审阅。

纯注释先检查执行AST、运行字符串、接口与资源无变化；只排除真正docstring并搜索其消费者。运行适用`ruff check`、`ruff format --check`、`mypy`或语法/导入检查以及课程链接核对。职责整理按受影响模块选择现有会话/模型/检索/审批/运行边界和tools/services测试；跨包或共享装配影响广时才扩大离线范围。复用M37核心实验观察关键关系，不为每个文件增加新测试。

默认模型0次，不因docstring改变文件hash重跑全部live。发现行为变化须单列影响、重新选择回归与必要真实内容。默认不更新云安装；若明确交付新安装，保留旧清单并执行现有精确发布要求，不能宣称源码与旧安装字节相同。

## 4. 什么时候停

两项覆盖规格范围并验收交付后停止。遇到需要新架构/业务扩展的问题记录，不混入注释工作；真实缺陷按范围与AGENTS处理，失败不能报通过。不自动启动M39。

## 5. 怎样交付

逐特性分支验收/中文提交/推送/合并/六SHA同步，main不复验。实施后写`handoffs/M38.md`及STATE，记录纯注释、职责整理、行为修复的实际区别和云安装状态。回退通过新分支revert，不修改已有业务事实/发布资产。完成后删除已被交接覆盖的PLAN和临时审阅资料。
