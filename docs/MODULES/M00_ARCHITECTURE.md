# M00｜源文档核对、系统边界与架构章程

**阶段：** A 基础

**前置：** 无；P00已有历史验收，不重新初始化

**来源：** 原 PDF 文件页 15–24、31–35、55–69、71–76。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 本模块目标
把原文变成后续所有会话共同遵守的架构基线。当前材料是说明性文章加截图，不是源码；验收目标是核心机制等价且可验证，不是复现作者的 95% 一次解决率或 99% AI 编码比例。

## 必须先完成的源文档决策
原文 p19 说 12 步，而 p57 的多轮详细图列出 13 步。采用 p57 的细化顺序作为本项目基线：INPUT_VALIDATE → SAFETY_CHECK → TEXT_CLEAN → BUILD_BIZ_CONTEXT → EVENT_CLUSTER → INTENT_RECOGNIZE → INTENT_CHECK → KEYWORD_CHECK → FETCH_SOP_ID → SOP_EXECUTE → REPLY_POLISH → SESSION_MAINTAIN → RESPONSE_ENVELOPE。第一版不实现多轮聚合时保留显式 passthrough，而不是悄悄改成另一套流水线。

p58 先聚合事件，p60 说明主意图服务只由 600 阶段统一调用。保留这一约束；不要让聚合器、槽位节点、SOP 各自再识别一遍主意图。p31–35、p41–52 对 FastText/LLM 的兜底表述有差异：先固定 Rule→Dense/Hybrid→Memory-augmented retrieval→Fallback 的外层契约，再用可配置策略接入 FastText 和 LLM。未知原始阈值与完整训练数据不得猜测。

明确记录原文与改造：内部 Spring/JIMDB/配置平台改为 Python/Redis/文件化配置；BGE-M3 可以先由 API Embedding 替代但不称相同向量空间；原文 128 字符截断、自动高置信回灌、裸加权公式都需独立风险记录。p52 最终 code 与 topK 第一候选不一致必须成为待解释问题和一致性测试，不能仿制这个输出。

## 架构需要具体落到哪些文件
生成模块边界和建议目录：domain（契约/实体）、application（用例/图）、adapters（模型/检索/DB/MCP）、sops、evals、tests、configs、scripts。只在 ModelGateway、IntentRetriever、SOPExecutor、ToolGateway、业务 Repository 等真实变化点定义 Port。不要为每一行代码创造抽象工厂。

固定 MySQL 业务事实源、Redis 可丢弃缓存、Milvus 派生投影、checkpoint 执行游标的边界。说明业务已完成但缓存更新失败、工具成功但进程崩溃、向量中的 ACTIVE 已过时各由谁纠正。规定业务事务和 outbox，不承诺跨系统分布式事务。

画开发依赖 DAG 和运行时状态图的文本规格：前者不可循环；后者允许有界 ReAct/重试/人工等待。给出系统的“停下来问人”分支和拒绝不安全执行条件。所有终结业务分支都要可记录并形成响应；无权限请求不能为他人建 case。

冻结主协议中必须存在的 ID、错误分类、版本、证据和预算字段，具体类型留给 M02。记录运行 thread 按 namespace+run_id 隔离；普通多轮对话用业务 question_id 关联，不能把整位用户塞进同一个可被任意消息恢复的审批线程。

## 范围与验收
范围覆盖 3–5 个核心合成客诉的 MVP，再扩展 15+ 场景及混合检索、多轮、FastText、审批恢复、评测闭环。排除美化 UI、企业登录、京 ME、真实金融操作、云端训练、复杂多 Agent 团队。

交付至少包括架构章程、原文差异 ADR、风险登记、分期功能矩阵、仓库目录职责、全局不变量和 M01/M02 的接口输入。验收通过标准不是“架构图很完整”，而是后续实现能够据此得到兼容的请求/响应与错误语义；用两个独立接口视角核对并留证据，可同会话完成，不强制增加会话数量。挑选“订单信息更正”“双问题交错”“审批后重放”三个场景走查，不能靠临时补丁解释状态。

## 接口与分期边界

M00 已完成设计验收，长期产物为 `ARCHITECTURE / CONTRACTS / DECISIONS / RISKS / SOURCE_MAP / ACCEPTANCE`。除非这些基线与新代码产生实质冲突，否则不重新规划 M00；需要复核时选择 `VERIFY_EXISTING`，只修改受影响的契约或决定。兼容性继续以两个接口视角和三个跨模块走查为准，不以文档数量或会话数量作为质量指标。
