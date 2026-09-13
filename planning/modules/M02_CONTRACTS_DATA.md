# M02｜领域契约、意图目录与从第一天开始的评测

**阶段：** A 基础

**前置：** M00, M01

**来源：** 原 PDF 文件页 25–30、51–54、55–66、71。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式

默认PLAN_MODULE，只设计/拆解本模块。使用[通用规划Prompt](../PLAN_MODULE_PROMPT.md)的六项交付格式。先读实仓AGENTS、CONTRACTS、PROJECT_STATE、DECISIONS及直接前置handoff；本文件不是完成证据。

本工作副本显式修订旧PG/部署/布局假设，采用现有MySQL、uv workspace与5GiB云Milvus受限实验；不重新部署、不自动付费、不把22阶段建成22个包。原PDF仅存本地，业务演示只用合成数据。

## 本模块目标
建立模块间唯一一版数据契约和可追溯的小型数据资产，阻止多个 Pro 会话各自命名状态、定义不同的 confidence、修改同一个意图 code。评测从这里开始，不等系统做完才补测试。

## 必须冻结的契约
RequestEnvelope 包含经过入口确认的 tenant/user/session、message_id、request_id、原文与时间；Question/Case 保存状态、实体、活动版本；EventContext 保存聚合后的文本、来源 message_ids、实体及冲突；IntentDecision 保存候选、各阶段证据、最终 code、decision（accept/clarify/handoff/reject）和原因；ToolRequest/ToolResult、SOPResult、ResponseEnvelope 有严格 schema。

区分 evidence_score（cosine/BM25/fusion 等带来源分数）、classifier_probability、经验证的 decision confidence。不能一律叫 confidence 再用同一阈值。定义 ExecutionBudget（时间、调用、token、tool步骤、重试）、EvidenceRef、VersionManifest。字段过多时分实体，但不丢掉拒识、未知值和失败可诊断性。

Question 状态至少覆盖 ACTIVE、WAITING_SLOT、WAITING_APPROVAL、RESOLVED、HANDED_OFF、CANCELLED；允许不适用状态被分期关闭。图运行状态与这些业务状态分开。同一 message 幂等接收，同一 operation_id 幂等执行。明确错误何时可重试、何时需问用户、何时人工接管。

## 意图和测试数据
依据 p25–30 的层级和例子构造合法的自有目录，不声称看到了完整原始表。code 稳定且唯一，层级用显式 l1/l2/l3/l4 与父子关系字段维护，不能从 CS_* 下划线数量猜业务层级。保留 is_actionable、required_slots、sop_id 和 registry_version。先选优惠未享受、优惠券不可用、订单活动查询等 3–5 类，用合成订单/券/商详 fixture 支撑真实逻辑，不照抄真实客户编号。

先写 30–60 条确定期望的 smoke/e2e 数据与开发集，并预留扩展到 500+ 的结构。数据每条含 case_id、来源/合成标记、variant_group、split、expected_intent、expected_entities、expected_tool_calls、expected_terminal_state。将同一问题的近义改写和同模板实例分在同一 split，避免训练/测试泄漏。测试集需人工或确定性业务规则核验，不能仅依赖生成它的模型再给自己打分。

## 评测口径先定义
分类正确率和 macro-F1；已接管样本的覆盖率/错误率；拒识与澄清率；实体保真；工具选择/参数正确率；脚本场景完成率；危险写操作误执行率；耗时与调用成本。不能把 Mock 场景完成率命名为真实线上一次解决率。硬安全门禁优先于综合分数。

## 必测与交付
非法枚举、未知 intentCode、缺 provenance、金额/编号类型损失、重复 message_id、相同 session 的两个 question、跨用户检索过滤都要有契约测试。为 p52 的 code/topK 不一致设一致性约束：最终选择若非 top1 必须有显式经过验证的路由理由，不能无声切换。

交付 schema/示例 JSON、意图注册表、少量冻结 fixture、数据版本清单、测试断言及 contracts 变更流程。共享 schema、全局 lock 和 DB migration 只能由本模块的主集成人统一发布。下游只能提变更请求，不能私自复制另一版 DTO。

## 本轮交付

按通用规划Prompt给详细设计、3–7项DAG、每项完整独立Astra执行Prompt、分层验收和交接。路径：`docs/MODULES/M02.md`、`handoffs/M02.md`、`reports/M02/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
