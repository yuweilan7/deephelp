# 从Java经验读懂DeepHelp

先读[从一条消息找到代码](source-navigation.md)，再按下表进入具体机制。

保留一套引擎。先用离线样本观察实际类型、决策、事实与trace，再由明确live入口运行真实模型；浏览文档、帮助、预览和pytest不会自动调用模型。开始异步基础可先读[M01指南](M01_ASYNC_GUIDE.md)。运行、工具和demo安装说明见[工具包](../../modules/deephelp-tools/README.md)。

课程面向资深Java开发者、Python语法入门者与Agent新人。先运行再读源码，每章都有前置、解释、类比边界、反例和练习答案；实验复用同一Conversation/领域类型。正常阅读和默认实验远程模型0次。当前第一批五章已交付，异步4–7与深化10–14按[唯一PLAN](../../planning/M33/PLAN.md)继续；完整标准见[M33规格](../MODULES/M33_JAVA_LEARNING_COURSE.md)。

| 章 | 学习问题 | 实验 |
|---|---|---|
| [01 上手](01-start.md) | 包、入口、对象如何装配 | course request |
| [02 值与类型](02-values.md) | 注解/校验/金额/Protocol | course types |
| [03 Python写法](03-python-patterns.md) | 生成器/闭包/装饰器/解包 | course syntax |
| [08 客诉主线](08-message-path.md) | 13段、槽位、原receipt | 同01的request输出 |
| [09 Agent基础](09-agent-basics.md) | 模型建议与代码守卫 | 同01的request输出 |

从根运行 `py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request`。第一次先看missing→answered→replay→unknown，再沿每章的当前源码链接阅读。M35/M36将消费同一课程；此处不配置云端或激活新发布。下面保留已有机制入口，历史M01指南是补充阅读。

| 机制 | 调用入口与核心函数 | 最小离线样本 | 观察点 |
|---|---|---|---|
| 13段主链 | [Conversation.run](../../modules/deephelp-app/src/deephelp_app/application/conversation/orchestrator.py)，[Cascade.recognize](../../modules/deephelp-app/src/deephelp_app/application/cascade.py) | `pytest modules/deephelp-app/tests/integration/test_m12_pipeline.py -q` | 清洗/实体、事件归属、600主意图、SOP与回复；缺槽位不得调用工具；主分类只在600 |
| Dense/BM25/Hybrid | [DenseRetriever](../../modules/deephelp-app/src/deephelp_app/application/dense.py)，[HybridRetriever](../../modules/deephelp-app/src/deephelp_app/application/hybrid.py)，[Milvus适配器](../../modules/deephelp-app/src/deephelp_app/adapters/retrieval/milvus_hybrid.py) | `python -m deephelp_tools.evaluation.retrieval.dense_cli preview`；`pytest modules/deephelp-app/tests/unit/retrieval/test_m09_hybrid.py -q` | scope/完整Embedding签名、原始分数与候选rank、融合权重；分数不是概率；冻结dev/test不作为运行数据 |
| MCP两端守卫 | [ToolGateway](../../modules/deephelp-app/src/deephelp_app/adapters/mcp/business/tool_gateway.py)，[协议schema](../../modules/deephelp-app/src/deephelp_app/adapters/mcp/business/mcp_protocol.py)，[demo服务](../../modules/deephelp-tools/src/deephelp_tools/demo/mcp_server.py) | `pytest modules/deephelp-app/tests/integration/test_m06_stdio.py -q` | 正式SDK list_tools/call_tool、可信上下文/签名、双端参数与结果事实校验、子进程退出 |
| 审批与恢复 | [ApprovalService](../../modules/deephelp-app/src/deephelp_app/application/approval.py)，[官方保存器](../../modules/deephelp-app/src/deephelp_app/adapters/approval/postgres_checkpoints.py) | `pytest modules/deephelp-app/tests/unit/approval/test_m15_approval.py -q` | 原M15 LangGraph/interrupt/resume机制；正式PG官方saver、审批绑定/唯一领取/UNKNOWN先对账；实际迁移恢复证据见M26 handoff，checkpoint不是账本 |
| 评测与审核飞轮 | [冻结评测](../../modules/deephelp-tools/src/deephelp_tools/evaluation/quality/core.py)，[审核](../../modules/deephelp-tools/src/deephelp_tools/evaluation/feedback/flywheel.py)，[三路构建](../../modules/deephelp-tools/src/deephelp_tools/evaluation/feedback/flywheel_assets.py) | `python -m deephelp_tools.evaluation.quality.evaluation_cli audit`；`pytest modules/deephelp-tools/tests/unit/feedback/test_m18_flywheel.py -q` | 候选不等于gold、source/variant与heldout隔离、各路人工审核、修订撤回和发布引用；训练不自动启用 |

以上命令从根通过 `py -3.14 -m uv run --locked` 执行，例如：

```powershell
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/integration/test_m12_pipeline.py -q
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.quality.evaluation_cli audit
```

领域类型与确定性安全规则在domain；会话/级联/SOP/审批在application；供应商、PostgreSQL/Redis/Milvus、MCP和trace在adapters；HTTP在api；配置、生命周期和CLI在bootstrap；运行策略、SOP/Prompt与SQL在resources。审批客户端与合成服务已经分开，查看一个HTTP请求时可以沿客户端、协议值、独立demo服务和审批账本追踪。

真实执行的边界是明确启动live探针或配置正式serve。模型执行方式、业务数据源与运行地点各自选择；有Key或位于服务器不会自动联网。Business/Retrieval服务的运行、schema、委托、预算回执及维护学习见[服务包](../../modules/deephelp-services/README.md)。M22工作台及M30个人云已交付，当前观测是M28原生Langfuse，旧M21 Tempo路线仅保留历史资料。整个13段主链不宣称全部由LangGraph实现；可读性改进按M32–M36实施，不因历史模块通过认定学习体验已满足。
