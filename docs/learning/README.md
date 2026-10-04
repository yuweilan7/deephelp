# DeepHelp 机制学习导航

保留一套引擎。先用离线样本观察实际类型、决策、事实与trace，再由明确live入口运行真实模型；浏览文档、帮助、预览和pytest不会自动调用模型。开始异步基础可先读[M01指南](M01_ASYNC_GUIDE.md)。运行、工具和demo安装说明见[工具包](../../modules/deephelp-tools/README.md)。

| 机制 | 调用入口与核心函数 | 最小离线样本 | 观察点 |
|---|---|---|---|
| 13段主链 | [Conversation.run](../../modules/deephelp-app/src/deephelp_app/application/conversation.py)，[Cascade.recognize](../../modules/deephelp-app/src/deephelp_app/application/cascade.py) | `pytest modules/deephelp-app/tests/integration/test_m12_pipeline.py -q` | 清洗/实体、事件归属、600主意图、SOP与回复；缺槽位不得调用工具；主分类只在600 |
| Dense/BM25/Hybrid | [DenseRetriever](../../modules/deephelp-app/src/deephelp_app/application/dense.py)，[HybridRetriever](../../modules/deephelp-app/src/deephelp_app/application/hybrid.py)，[Milvus适配器](../../modules/deephelp-app/src/deephelp_app/adapters/milvus_hybrid.py) | `python -m deephelp_tools.evaluation.dense_cli preview`；`pytest modules/deephelp-app/tests/unit/test_m09_hybrid.py -q` | scope/完整Embedding签名、原始分数与候选rank、融合权重；分数不是概率；冻结dev/test不作为运行数据 |
| MCP两端守卫 | [ToolGateway](../../modules/deephelp-app/src/deephelp_app/adapters/tool_gateway.py)，[协议schema](../../modules/deephelp-app/src/deephelp_app/adapters/mcp_protocol.py)，[demo服务](../../modules/deephelp-tools/src/deephelp_tools/demo/mcp_server.py) | `pytest modules/deephelp-app/tests/integration/test_m06_stdio.py -q` | 正式SDK list_tools/call_tool、可信上下文/签名、双端参数与结果事实校验、子进程退出 |
| 审批与恢复 | [ApprovalService](../../modules/deephelp-app/src/deephelp_app/application/approval.py)，[MySQLSaver](../../modules/deephelp-app/src/deephelp_app/adapters/checkpoints.py) | `pytest modules/deephelp-app/tests/unit/test_m15_approval.py -q` | 实际LangGraph StateGraph/interrupt/resume，审批版本绑定、唯一领取与UNKNOWN先对账；checkpoint不是账本 |
| 评测与审核飞轮 | [冻结评测](../../modules/deephelp-tools/src/deephelp_tools/evaluation/core.py)，[审核](../../modules/deephelp-tools/src/deephelp_tools/evaluation/flywheel.py)，[三路构建](../../modules/deephelp-tools/src/deephelp_tools/evaluation/flywheel_assets.py) | `python -m deephelp_tools.evaluation.evaluation_cli audit`；`pytest modules/deephelp-tools/tests/unit/test_m18_flywheel.py -q` | 候选不等于gold、source/variant与heldout隔离、各路人工审核、修订撤回和发布引用；训练不自动启用 |

以上命令从根通过 `py -3.14 -m uv run --locked` 执行，例如：

```powershell
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/integration/test_m12_pipeline.py -q
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.evaluation_cli audit
```

领域类型与确定性安全规则在domain；会话/级联/SOP/审批在application；供应商、MySQL/Redis/Milvus、MCP和trace在adapters；HTTP在api；配置、生命周期和CLI在bootstrap；运行策略、SOP/Prompt与SQL在resources。审批客户端与合成服务已经分开，查看一个HTTP请求时可以沿客户端、协议值、独立demo服务和审批账本追踪。

真实执行的边界是明确启动live探针或配置正式serve。模型执行方式、业务数据源与运行地点各自选择；有Key或位于服务器不会自动联网。M19未建设前端按钮、M20业务数据库、M21观测平台或部署。整个13段主链也不宣称全部由LangGraph实现。
