# M10｜多层记忆、Question 生命周期与状态事实源

**阶段：** C 核心深化

**前置：** M02, M08

**来源：** 原 PDF 文件页 55–66、69。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式

默认PLAN_MODULE，只设计/拆解本模块。使用[通用规划Prompt](../PLAN_MODULE_PROMPT.md)的六项交付格式。先读实仓AGENTS、CONTRACTS、PROJECT_STATE、DECISIONS及直接前置handoff；本文件不是完成证据。

本工作副本显式修订旧PG/部署/布局假设，采用现有MySQL、uv workspace与5GiB云Milvus受限实验；不重新部署、不自动付费、不把22阶段建成22个包。原PDF仅存本地，业务演示只用合成数据。

## 本模块目标
让“同一个用户在说哪个问题、哪些信息已确认、问题何时结束”成为明确的业务状态，而不是把历史聊天全部塞进大模型。复刻 JIMDB/DB/Milvus 的职责分工，用 Redis/MySQL/向量投影实现。

## 身份与生命周期
tenant/user/session/question/message 的作用、幂等键和唯一约束遵循 M02。一个 session 可有多个 ACTIVE question，不能只有一个 current_question 字符串覆盖全部。实体更正有来源和版本，原订单的记录不能被新订单静默污染。

状态转移定义 ACTIVE↔WAITING_SLOT、WAITING_APPROVAL、RESOLVED、HANDED_OFF、CANCELLED；RESOLVED 后新消息何时新建问题、何时显式重开，要有策略和审计。TTL 只管缓存保留，不等于业务自动解决。确定结束条件以工具事实/用户确认/流程结果为依据，不因模型语气肯定就结束。

## 三层存储职责
MySQL 保存 messages、cases、entities、status/version、operation/approval 基础结构；Redis 保存有 TTL 的短期历史窗口、关键词/活跃 case 缓存，缓存丢失可从 MySQL 重建；Milvus 保存可重建的事件向量/摘要与 status/version 投影。原文 zset/query/keywords/questionId 关系可以保留功能，但不能用 Redis 唯一保存幂等锁、资金结果或审批。

用同一业务事务写 outbox，再由轻量 worker 幂等更新 Redis/Milvus。投影更新失败、重试和乱序应按 event/version 处理。检索命中后回查 MySQL 的归属/状态/版本，避免已解决问题仍以 ACTIVE 被召回。不要承诺在 MySQL+Redis+Milvus 上天然强一致。

## 记忆读取
提供 MemoryPort/CaseRepository：获取有界历史、已确认实体、活跃问题列表、相关已结问题摘要。按 token/条数/时间限制窗口；引用保留 message_id，让错误聚合可还原。摘要是派生数据，不能覆盖原始事实或合并冲突编号。

## 必测案例
同用户两问题交错、不同用户相同订单字符串、重复消息、乱序补充、实体更正、Redis flush 后恢复、Milvus stale ACTIVE、outbox 重复/乱序、进程重启、关闭问题后新起相似问题、长历史裁剪。并发修改至少采用版本检查或等价受控串行，不能 last-write-wins 丢掉确认信息。

交付业务状态图、表/schema 与约束、缓存键规范及 TTL、outbox 投影契约、回放/清理策略和实际 MySQL/Redis 集成测试。checkpoint 接入留 M15，先把业务事实权威说清楚。

## 本轮交付

按通用规划Prompt给详细设计、3–7项DAG、每项完整独立Astra执行Prompt、分层验收和交接。路径：`docs/MODULES/M10.md`、`handoffs/M10.md`、`reports/M10/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
