# M10｜多层记忆、Question 生命周期与状态事实源

**阶段：** C 核心深化

**前置：** M02, M08

**来源：** 原 PDF 文件页 55–66、69。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式（v2）

默认 MODE=PLAN_MODULE。实际读取 AGENTS、CONTRACTS、PROJECT_STATE、当前相关代码/测试与直接前置 handoff，按[通用规划 Prompt](../PLAN_MODULE_PROMPT.md)先选择 DIRECT / DECOMPOSE / PROBE_FIRST / VERIFY_EXISTING，再给最小充分设计和实施任务；不强制拆分。明确要求实施时按本轮授权执行，不继续生成下一层规划。

原图按模块页码读取 `.local/references/deephelp-original.pdf` 或本轮 PDF 附件；GitHub 访问不包含被忽略的本地文件。已核对的原图不必每个 S 重读，旧解压稿仅为历史来源。保留现有 MySQL、uv workspace 和已记录的云 Milvus 实验，不重建 P00。一个 M 默认一个主会话顺序实施，相关测试随每个任务交付；跨会话从实际文件与最新合法 HEAD 接续。

## 本模块目标
让“同一个用户在说哪个问题、哪些信息已确认、问题何时结束”成为明确的业务状态，而不是把历史聊天全部塞进大模型。复刻 JIMDB/DB/Milvus 的职责分工，用 Redis/MySQL/向量投影实现。

## 身份与生命周期
tenant/user/session/question/message 的作用、幂等键和唯一约束遵循 M02。一个 session 可有多个开放 question（ACTIVE、WAITING_SLOT，及仅作受控上下文的 WAITING_APPROVAL），不能只有一个 current_question 字符串覆盖全部。实体更正有来源和版本，原订单的记录不能被新订单静默污染。

状态转移定义 ACTIVE↔WAITING_SLOT、WAITING_APPROVAL、RESOLVED、HANDED_OFF、CANCELLED；RESOLVED 后新消息何时新建问题、何时显式重开，要有策略和审计。TTL 只管缓存保留，不等于业务自动解决。确定结束条件以工具事实/用户确认/流程结果为依据，不因模型语气肯定就结束。

## 三层存储职责
MySQL 保存 messages、cases、entities、status/version、operation/approval 基础结构；Redis 保存有 TTL 的短期历史窗口、关键词/活跃 case 缓存，缓存丢失可从 MySQL 重建；Milvus 保存可重建的事件向量/摘要与 status/version 投影。原文 zset/query/keywords/questionId 关系可以保留功能，但不能用 Redis 唯一保存幂等锁、资金结果或审批。

用同一业务事务写 outbox，再由轻量 worker 幂等更新 Redis/Milvus。投影更新失败、重试和乱序应按 event/version 处理。检索命中后回查 MySQL 的归属/状态/版本，避免已解决问题仍以 ACTIVE 被召回。不要承诺在 MySQL+Redis+Milvus 上天然强一致。

## 记忆读取
提供 MemoryPort/CaseRepository：获取有界历史、已确认实体、活跃问题列表、相关已结问题摘要。按 token/条数/时间限制窗口；引用保留 message_id，让错误聚合可还原。摘要是派生数据，不能覆盖原始事实或合并冲突编号。

## 必测案例
同用户两问题交错、不同用户相同订单字符串、重复消息、乱序补充、实体更正、隔离缓存丢失后恢复、Milvus stale ACTIVE、outbox 重复/乱序、进程重启、关闭问题后新起相似问题、长历史裁剪。并发修改至少采用版本检查或等价受控串行，不能 last-write-wins 丢掉确认信息。

交付业务状态图、表/schema 与约束、缓存键规范及 TTL、outbox 投影契约、回放/清理策略和实际 MySQL/Redis 集成测试。checkpoint 接入留 M15，先把业务事实权威说清楚。

## 接口与分期边界（v2复核）

“开放问题”明确包含 ACTIVE、WAITING_SLOT；WAITING_APPROVAL 只作受授权的上下文候选，普通新消息不能批准/恢复挂起run。原图 ACTIVE 过滤在本项目扩展状态机中不是直接照搬的枚举条件。基于 M08 账本增量迁移，审批执行逻辑仍留 M15。缓存丢失通过隔离测试实例或本测试专用前缀模拟，禁止对共享 Redis 执行 FLUSHALL/FLUSHDB。

## 本轮交付

按通用规划 Prompt 自适应决定任务粒度；可只给一份实施任务，需要拆分时才给最少必要的 S 和依赖。已有成果先验收/补差异，不重复建设。保留本模块全部关键失败案例和适用的分层验收。路径：`docs/MODULES/M10.md`、`handoffs/M10.md`、`reports/M10/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
