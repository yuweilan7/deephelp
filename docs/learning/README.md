# DeepHelp 机制学习导航

先读[从一条消息找到代码](source-navigation.md)，再按下表进入具体机制。

保留一套引擎。先用离线样本观察实际类型、决策、事实与trace，再由明确live入口运行真实模型；浏览文档、帮助、预览和pytest不会自动调用模型。开始异步基础可先读[M01指南](M01_ASYNC_GUIDE.md)。运行、工具和demo安装说明见[工具包](../../modules/deephelp-tools/README.md)。

当前这里主要是一页异步说明和机制索引，尚未达到面向Java开发者的详细课程目标。2026-10-05新增[M33课程规格](../MODULES/M33_JAVA_LEARNING_COURSE.md)和[实施计划](../../planning/M33/PLAN.md)：先读懂一次请求/类型/高级写法，再跑异步时序/取消/资源实验，最后学习Agent、检索、记忆、审批和发布；核心源码注释与实验同步交付。[M32](../MODULES/M32_CODE_ORGANIZATION.md)先整理职责与路径，[M35](../MODULES/M35_FRONTEND_FOUNDATION.md)/[M36](../MODULES/M36_EXPLAINABLE_WORKBENCH.md)再让教程与实际页面/run对应。以下仍是当前可用入口，新增课程尚未实施。

| 机制 | 调用入口与核心函数 | 最小离线样本 | 观察点 |
|---|---|---|---|
| 13段主链 | [Conversation.run](../../modules/deephelp-app/src/deephelp_app/application/conversation/orchestrator.py)，[Cascade.recognize](../../modules/deephelp-app/src/deephelp_app/application/cascade.py) | `pytest modules/deephelp-app/tests/integration/test_m12_pipeline.py -q` | 清洗/实体、事件归属、600主意图、SOP与回复；缺槽位不得调用工具；主分类只在600 |
| Dense/BM25/Hybrid | [DenseRetriever](../../modules/deephelp-app/src/deephelp_app/application/dense.py)，[HybridRetriever](../../modules/deephelp-app/src/deephelp_app/application/hybrid.py)，[Milvus适配器](../../modules/deephelp-app/src/deephelp_app/adapters/retrieval/milvus_hybrid.py) | `python -m deephelp_tools.evaluation.retrieval.dense_cli preview`；`pytest modules/deephelp-app/tests/unit/test_m09_hybrid.py -q` | scope/完整Embedding签名、原始分数与候选rank、融合权重；分数不是概率；冻结dev/test不作为运行数据 |
| MCP两端守卫 | [ToolGateway](../../modules/deephelp-app/src/deephelp_app/adapters/mcp/business/tool_gateway.py)，[协议schema](../../modules/deephelp-app/src/deephelp_app/adapters/mcp/business/mcp_protocol.py)，[demo服务](../../modules/deephelp-tools/src/deephelp_tools/demo/mcp_server.py) | `pytest modules/deephelp-app/tests/integration/test_m06_stdio.py -q` | 正式SDK list_tools/call_tool、可信上下文/签名、双端参数与结果事实校验、子进程退出 |
| 审批与恢复 | [ApprovalService](../../modules/deephelp-app/src/deephelp_app/application/approval.py)，[官方保存器](../../modules/deephelp-app/src/deephelp_app/adapters/approval/postgres_checkpoints.py) | `pytest modules/deephelp-app/tests/unit/test_m15_approval.py -q` | 原M15 LangGraph/interrupt/resume机制；正式PG官方saver、审批绑定/唯一领取/UNKNOWN先对账；实际迁移恢复证据见M26 handoff，checkpoint不是账本 |
| 评测与审核飞轮 | [冻结评测](../../modules/deephelp-tools/src/deephelp_tools/evaluation/quality/core.py)，[审核](../../modules/deephelp-tools/src/deephelp_tools/evaluation/feedback/flywheel.py)，[三路构建](../../modules/deephelp-tools/src/deephelp_tools/evaluation/feedback/flywheel_assets.py) | `python -m deephelp_tools.evaluation.quality.evaluation_cli audit`；`pytest modules/deephelp-tools/tests/unit/test_m18_flywheel.py -q` | 候选不等于gold、source/variant与heldout隔离、各路人工审核、修订撤回和发布引用；训练不自动启用 |

以上命令从根通过 `py -3.14 -m uv run --locked` 执行，例如：

```powershell
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/integration/test_m12_pipeline.py -q
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.quality.evaluation_cli audit
```

领域类型与确定性安全规则在domain；会话/级联/SOP/审批在application；供应商、PostgreSQL/Redis/Milvus、MCP和trace在adapters；HTTP在api；配置、生命周期和CLI在bootstrap；运行策略、SOP/Prompt与SQL在resources。审批客户端与合成服务已经分开，查看一个HTTP请求时可以沿客户端、协议值、独立demo服务和审批账本追踪。

真实执行的边界是明确启动live探针或配置正式serve。模型执行方式、业务数据源与运行地点各自选择；有Key或位于服务器不会自动联网。Business/Retrieval服务的运行、schema、委托、预算回执及维护学习见[服务包](../../modules/deephelp-services/README.md)。M22工作台及M30个人云已交付，当前观测是M28原生Langfuse，旧M21 Tempo路线仅保留历史资料。整个13段主链不宣称全部由LangGraph实现；可读性改进按M32–M36实施，不因历史模块通过认定学习体验已满足。
