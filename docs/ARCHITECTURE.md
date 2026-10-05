# DeepHelp 架构章程

本文件说明当前核心机制和职责，不列实施步骤或历史部署参数。实际安装/已验证范围见[STATE](PROJECT_STATE.md)，接口细节见[CONTRACTS](CONTRACTS.md)，为什么作这些选择见[DECISIONS](DECISIONS.md)，PDF来源见[SOURCE_MAP](SOURCE_MAP.md)。旧MySQL/OTel设计和当时命令由Git及对应handoff追溯。

## 1. 目标与边界

复刻意图识别、多轮问题归属、分层记忆、SOP/MCP、调试和评测，用合成客诉学习真实协议/模型与确定性守卫。默认只读，不接企业支付/任意退款发券。个人免登录简化交互，服务端身份、归属、同源/CSRF、审批和幂等继续生效。

本地用固定响应、内存适配器和原引擎解释核心原理；云端提供当前完整教学。两者按用途验收，不要求本机复制全栈。正式模型/数据库失败关闭，只有显式离线入口使用回放，不自动伪装成成功业务。

## 2. 物理目录与依赖方向

根uv workspace、单一锁与三个包：app拥有domain/application/adapters/api/bootstrap/resources；services提供独立Business/Retrieval MCP；tools提供learning/demo/probes/evaluation。源码路径见[导航](learning/source-navigation.md)，安装/资源边界见[工具包](../modules/deephelp-tools/README.md)。

依赖为application→domain、adapters→domain，由bootstrap装配；领域不反向依赖HTTP、模型或数据库SDK。DTO只含数据，连接、锁、时钟和客户端由运行资源拥有。Port只放在真实变化边界；薄CLI复用同一main，领域类型和引擎不复制。

Business/Retrieval服务通过正式MCP协议、可信委托、双端schema/语义/归属校验复用原能力，不提供任意SQL/缓存操作，也不把账本事务搬到远端工具。AsyncExitStack/lifespan拥有连接池和SDK会话，请求只借用；取消后等待本请求资源收口，不能关闭共享client或遗留兄弟任务。

## 3. 主流程：13段，不等于13次模型调用

| 阶段 | 职责与关键边界 |
|---|---|
| 100 INPUT_VALIDATE | 参数/ID/基础身份；非法输入不建业务问题 |
| 200 SAFETY_CHECK | 已验证身份的权限；拒绝不为他人建case |
| 300 TEXT_CLEAN | 保留原文、实体来源/更正/否定；未知不猜测 |
| 400 BUILD_BIZ_CONTEXT | 读取受授权历史；事实库失败不回退JSON |
| 500 EVENT_CLUSTER | 候选窗口/硬约束/归属聚合；歧义澄清，不替主意图分类 |
| 600 INTENT_RECOGNIZE | 唯一主级联入口；内部模型/检索尝试独立计量，可拒识 |
| 700 INTENT_CHECK | 注册表/可执行性/决策一致性；未知不编路由 |
| 800 KEYWORD_CHECK | 槽位/冲突；缺槽禁止调用SOP业务工具 |
| 900 FETCH_SOP_ID | 固定SOP版本/工具许可；无有效版本转人工 |
| 1000 SOP_EXECUTE | 有界ReAct/固定步骤、工具/证据；写操作先审批及强校验 |
| 1100 REPLY_POLISH | 关键事实由结构化字段/模板输出，润色不得新增事实 |
| 1200 SESSION_MAINTAIN | 短事务保存终态/问题版本/outbox；失败不称已保存 |
| 1300 RESPONSE_ENVELOPE | 同源结果/引用/诊断，重投复用原receipt |

已接收请求的早停仍走适用终态保存；未授权输入只返回错误/安全审计，数据库失效不能承诺强行持久化。500归属、1000工具选择与1100润色不重新作主分类。整条主链不等于全部由LangGraph执行。

## 4. 状态与入口

Question的ACTIVE/WAITING_SLOT/WAITING_APPROVAL/RESOLVED/HANDED_OFF/CANCELLED与Run、响应outcome分别维护。一个session可有多个问题；同订单不等于同问题，歧义消息可以先保存而不随机绑定question。等待槽位正常结束当前run，不挂住人工等待资源。

普通消息、明确审批、恢复和对账是不同入口。审批绑定原operation/run、参数/SOP/question版本、主体及期限；CAS决定与唯一执行领取分责。普通新消息不批准旧操作；图线程由服务端映射原run，checkpoint不是批准或执行事实。批准后恢复原操作，不新建业务效果。

## 5. 数据所有权与一致性

| 数据 | 权威与维护者 | 派生/边界 |
|---|---|---|
| message/run/question、实体版本、receipt | PostgreSQL账本及业务用例 | Redis窗口/Milvus事件摘要可重建 |
| 审批、operation、下游效果/审计 | PostgreSQL账本与下游幂等/查询协议 | checkpoint不代替批准、领取或效果 |
| outbox与消费进度 | 与业务变更同一PG短事务 | 投影重复幂等、旧版本忽略，缺口回查账本 |
| LangGraph状态/父checkpoint/pending writes | 官方AsyncPostgresSaver，独立schema | 恢复前查账本，不盲目重演副作用 |
| SOP/Prompt/语料/模型/安装绑定 | 不可变发布资产、manifest及PG active/previous | 平台候选/latest与缓存不成为第二active |
| Langfuse/指标/调试trace | 诊断记录，按原身份/run读取 | 丢失不改变业务效果，不补发业务 |

事务/行锁不跨模型、网络工具或人工等待；更新用CAS/唯一键，Redis锁不承载正确性。PG已提交而投影/观测失败时保留已持久事实，由outbox修投影、诊断标缺失。活动候选须回查归属/状态/version，不能凭相似度串单。

工具结果未知先按原operation查询对账；checkpoint落后不重新执行。只在下游幂等/查询协议所定义的范围保证效果不重复，不宣称通用exactly-once或任意RUNNING重领。旧数据/安装/资产有恢复或历史消费者时保留，当前正式单写PG。

## 6. 模型与资源边界

权限、状态、版本、工具许可、金额/证据、预算/超时和循环边界由确定性代码守护，模型负责已授权的抽取、归属、候选决策、工具选择及受限措辞。Embedding按签名隔离向量空间；Rerank只能排序已召回候选，保留原分数/来源，不能修复未召回文档。

请求沿用同一共享预算/deadline，SDK/HTTP/节点重试不能相乘；未知用量不释放预留，副作用不套通用重试。具体参数由当前任务配置决定，旧试验金额、资源配额和时限不固化为长期默认。能力/签名见[MODEL_CAPABILITIES](MODEL_CAPABILITIES.md)，选择和验收见AGENTS/ROADMAP。

## 7. 验收入口

[ROADMAP影响表](ROADMAP.md#按影响选择验收)选择验证范围；[ACCEPTANCE](ACCEPTANCE.md)保留设计难例，实际结果由handoff证明。运行见LOCAL_SETUP，协作见AGENTS。容量/恢复/合成样本结论只适用于其已测负载，当前限制见STATE。
