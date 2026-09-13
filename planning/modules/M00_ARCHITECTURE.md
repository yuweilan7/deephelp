# M00｜源文档核对、系统边界与架构章程

**阶段：** A 基础

**前置：** 无；P00已有历史验收，不重新初始化

**来源：** 原 PDF 文件页 15–24、31–35、55–69、71–76。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式（v2）

默认 MODE=PLAN_MODULE。实际读取 AGENTS、CONTRACTS、PROJECT_STATE、当前相关代码/测试与直接前置 handoff，按[通用规划 Prompt](../PLAN_MODULE_PROMPT.md)先选择 DIRECT / DECOMPOSE / PROBE_FIRST / VERIFY_EXISTING，再给最小充分设计和实施任务；不强制拆分。明确要求实施时按本轮授权执行，不继续生成下一层规划。

原图按模块页码读取 `.local/references/deephelp-original.pdf` 或本轮 PDF 附件；GitHub 访问不包含被忽略的本地文件。已核对的原图不必每个 S 重读，旧解压稿仅为历史来源。保留现有 MySQL、uv workspace 和已记录的云 Milvus 实验，不重建 P00。一个 M 默认一个主会话顺序实施，相关测试随每个任务交付；跨会话从实际文件与最新合法 HEAD 接续。

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

## 接口与分期边界（v2复核）

当前已有 M00 设计和文档工具，优先判断 VERIFY_EXISTING。S06 必须列出 S01–S05 的要求—既有产物—本轮检查—缺口；缺失项调用对应任务补齐，不要求重新开五个会话。S01 核来源与差异，S05 检文档/上下文/原件工具；两者都不是 Agent 业务实现。兼容性以可复查的两种接口视角和三个走查为依据，不把会话数量当质量指标。

## 本轮交付

按通用规划 Prompt 自适应决定任务粒度；可只给一份实施任务，需要拆分时才给最少必要的 S 和依赖。已有成果先验收/补差异，不重复建设。保留本模块全部关键失败案例和适用的分层验收。路径：`docs/MODULES/M00.md`、`handoffs/M00.md`、`reports/M00/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
