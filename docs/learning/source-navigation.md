# 从一条消息找到代码

先顺着一次请求阅读，再进入具体机制；同一领域对象和同一会话引擎贯穿应用、MCP与实验。

## 消息与事实的调用路径

1. [API](../../modules/deephelp-app/src/deephelp_app/api/app.py)接收不可信输入，从入口注入身份；正文或trace ID不能授权。
2. [Conversation.run](../../modules/deephelp-app/src/deephelp_app/application/conversation/orchestrator.py)是13段编排入口。先授权hint，查原receipt，再领取新消息；重投返回原结果，不重跑模型或工具。
3. [preparation](../../modules/deephelp-app/src/deephelp_app/application/conversation/preparation.py)在原子领取前加载窗口和判定归属；[state](../../modules/deephelp-app/src/deephelp_app/application/conversation/state.py)把实体、冲突、来源和固定版本推进到当前问题。一次请求拥有一个可变QuestionState，里面的Question仍是不可变DTO。每个await完成的状态即刻写入该请求对象，后续失败仍保留已经得到的诊断。
4. [事件归属](../../modules/deephelp-app/src/deephelp_app/application/event_cluster/aggregation.py)只决定归属；fragments分诉求，window裁剪窗口，components约束合并，judge调用结构化判断。主意图只在Conversation的600调用[IntentService](../../modules/deephelp-app/src/deephelp_app/application/intent.py)。
5. [SOPExecutor](../../modules/deephelp-app/src/deephelp_app/application/sop.py)检查版本、槽位与可信上下文后派发固定工具；[事实检查](../../modules/deephelp-app/src/deephelp_app/domain/checks.py)要求金额、事实和证据闭合，缺槽不得调用工具。
6. [terminal](../../modules/deephelp-app/src/deephelp_app/application/conversation/terminal.py)先保存原账本终态，再采集[诊断](../../modules/deephelp-app/src/deephelp_app/application/conversation/diagnostics.py)。短时保存任务由当前请求创建并等待；shield允许取消时等它落盘，不允许继续执行模型/工具。run退出时重置ContextVar，防止下一条消息借用原计数。

Conversation保留stage/prepare等原方法入口，绑定的是对应职责模块的函数。Python把类中的函数绑定到实例，调用时自动传入self；它相当于Java对象将调用委托给同一组组件，没有继承第二套业务引擎。需要查计数和取消时，直接看[tracking](../../modules/deephelp-app/src/deephelp_app/application/conversation/tracking.py)与[stages](../../modules/deephelp-app/src/deephelp_app/application/conversation/stages.py)。

## 唯一类型的责任

[domain/models](../../modules/deephelp-app/src/deephelp_app/domain/models/__init__.py)只显式导出原公开名字；定义按概念保存在同目录的具体模块，内部import直接指向它们。

| 要查的规则 | 定义入口 | 向下依赖 |
|---|---|---|
| 身份、状态、预算、金额 | common | Pydantic与标准库；没有连接或锁 |
| 原文、实体与来源 | entities、text | 公共值及意图目录 |
| 意图目录、最终选择与级联 | intents、intent_decisions | 实体、证据和实际retrieval结果 |
| Chat、Embedding、Rerank输入输出 | model_io | 公共值；不持有HTTP客户端 |
| 检索候选、签名与MCP回执 | retrieval、retrieval_protocol | 模型值、意图目录、请求和预算 |
| 问题、窗口与归属图 | conversation、memory、events | 实体、目录和固定版本 |
| 工具、SOP、审批与响应 | tools、sop、approval、responses | 参数hash、事实/证据、状态及固定版本 |
| 发布与业务维护 | release、business | 不可变清单与业务值；不负责装配或事务 |

从公共值向具体业务类型单向导入；responses最后组合请求结果，不要求其他DTO反向导入它。领域包的__init__保持原import API，类型对象与职责模块中的对象相同。

## 从用例进入子系统

上层边界仍是domain/application/adapters/api/bootstrap/resources；下面的目录承接具体依赖。配置选择与资源生命周期在bootstrap/runtime，入口参数在bootstrap/commands。导入这些模块不打开数据库、模型或子进程。

| 责任 | 实际入口 | 调用者与失败处理 |
|---|---|---|
| 模型网关 | [model_gateway/gateway](../../modules/deephelp-app/src/deephelp_app/adapters/model_gateway/gateway.py)、[validation](../../modules/deephelp-app/src/deephelp_app/adapters/model_gateway/validation.py)、providers、fasttext_runtime | 用例经Port调用；协议、schema、usage由适配器验证，AppError交给原用例终态处理 |
| PG事实 | [persistence/ledger](../../modules/deephelp-app/src/deephelp_app/adapters/persistence/ledger.py)、cases、history、postgres | 接收/终态事务和问题版本归PG；历史API只读原事实，错误不回退demo |
| 记忆与检索 | memory_projection/redis_memory、milvus_memory；[retrieval/milvus_hybrid](../../modules/deephelp-app/src/deephelp_app/adapters/retrieval/milvus_hybrid.py)、milvus_dense | 用例决定召回；适配器处理索引协议。投影可重建，失败不篡改原PG账本 |
| MCP | [mcp/business/tool_gateway](../../modules/deephelp-app/src/deephelp_app/adapters/mcp/business/tool_gateway.py)、business_client、mcp_protocol；mcp/retrieval；mcp/shared/network_mcp | 客户端、双端schema与可信委托各有入口；预算未知仍保留预留，业务结果由原事实/证据守卫处理 |
| 审批 | [approval/postgres_checkpoints](../../modules/deephelp-app/src/deephelp_app/adapters/approval/postgres_checkpoints.py)、approval_store、checkpoints、synthetic_rights | ApprovalService显式暂停/恢复；checkpoint不是账本，UNKNOWN先查询对账，普通新消息不批准旧操作 |
| 观测 | [observability/telemetry](../../modules/deephelp-app/src/deephelp_app/adapters/observability/telemetry.py)、native_observation、langfuse_query、trace、prometheus | 埋点和查询解释实际执行；原用例处理诊断缺失，不让平台失败改变业务副作用 |
| 完整发布 | [releases/release_assets](../../modules/deephelp-app/src/deephelp_app/adapters/releases/release_assets.py)、asset_integrity、release_store、runtime_assets、feedback_store | 运行装配校验不可变字节/引用/active CAS；tools只在显式准备时参与，旧清单不绑定新源码 |
| 运行装配 | [runtime/assembly](../../modules/deephelp-app/src/deephelp_app/bootstrap/runtime/assembly.py)、auth、budget、event_runtime、release_runtime | assembly拥有AsyncExitStack并逆序关闭依赖；auth选择原身份，budget在派发前保存累计预留 |
| 独立服务 | [Business API](../../modules/deephelp-services/src/deephelp_services/business/api.py)、repository；[Retrieval API](../../modules/deephelp-services/src/deephelp_services/retrieval/api.py)、backend；transport/http | Business负责关系维护/查询事务，Retrieval负责冻结语料检索/回执；共享传输只管会话、origin和认证挂载 |

tools/evaluation内部按retrieval、quality、feedback、releases、migration、platform归组；probes按models、retrieval、protocol、conversation、approval、releases、platform、migration归组。learning/workbench组合原协议/分析/评测路由；learning/replay、faults、fakes提供显式实验。正式app没有反向tools/services导入，业务装配仍复用同一组领域类型与用例。

原bootstrap/*_cli和tools下已有CLI名字是受支持的命令入口，文件只转调职责模块中的main，具体实现只有一处。消费者包括旧运行文档、部署start.sh、显式任务与历史恢复命令；这不是等待删除的临时别名。新实现路径同样支持明确执行，内部代码和patch定位直接使用新路径。Business/Retrieval/workbench原模块路径变成包，由__main__继续承接受支持的`python -m`命令。找逻辑时进入表中实现，不在薄入口增加业务代码。

## 离线定位练习

从仓库根运行：

```powershell
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/integration/test_m12_pipeline.py modules/deephelp-app/tests/unit/test_m32_structure.py --tb=short -q
```

观察原测试中的缺槽→补槽、更正、重投、600计数和模型/工具调用断言，再在上述路径逐步找到实现。这里复用原离线引擎及合成事实，证明组织后的行为与边界，不代表远程模型质量或新云版本。旧完整发布绑定旧运行字节；本轮源码指纹变了，应保留原安装或在未来正式发布时重新取得精确内容证据。
