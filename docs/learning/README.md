# 从Java经验读懂DeepHelp

先读[从一条消息找到代码](source-navigation.md)，再按下表进入具体机制。

保留一套引擎。先用离线样本观察实际类型、决策、事实与trace，再由明确live入口运行真实模型；浏览文档、帮助、预览和pytest不会自动调用模型。开始异步基础可先读[M01指南](M01_ASYNC_GUIDE.md)。运行、工具和demo安装说明见[工具包](../../modules/deephelp-tools/README.md)。

课程面向资深Java开发者、Python语法入门者与Agent新人。先运行再读源码，每章都有前置、普通话解释、Java类比边界、反例、练习答案和当前源码；14章全部交付。异步章还有时序图与任务/资源拥有者表。实验复用同一Conversation/领域类型，正常阅读和默认实验远程模型0次。实际范围/检查见[M33交接](../../handoffs/M33.md)，完整标准见[M33规格](../MODULES/M33_JAVA_LEARNING_COURSE.md)。

| 章 | 学习问题 | 实验 |
|---|---|---|
| [01 上手](01-start.md) | 包、入口、对象如何装配 | course request |
| [02 值与类型](02-values.md) | 注解/校验/金额/Protocol | course types |
| [03 Python写法](03-python-patterns.md) | 生成器/闭包/装饰器/解包 | course syntax |
| [04 awaitable](04-awaitables.md) | coroutine/Task/Future/loop | course awaitables |
| [05 协调](05-coordination.md) | TaskGroup/gather/Event/锁/限流 | course coordination |
| [06 取消](06-cancellation.md) | deadline/timeout/shield/线程 | course cancellation |
| [07 资源](07-resources.md) | 池/事务/stdio/退出栈 | course resources |
| [08 客诉主线](08-message-path.md) | 13段、槽位、原receipt | 同01的request输出 |
| [09 Agent基础](09-agent-basics.md) | 模型建议与代码守卫 | 同01的request输出 |
| [10 检索](10-retrieval.md) | 宽召回/重排/意图归并/最终确认 | course retrieval |
| [11 记忆](11-memory.md) | 交错问题/账本/outbox/投影 | course memory |
| [12 审批](12-approval.md) | interrupt/官方saver/UNKNOWN对账 | 原ApprovalService三种UNKNOWN测试 |
| [13 观测](13-observations.md) | receipt/run/trace/原证据/usage | course observations |
| [14 评测发布](14-evaluation-release.md) | 留出/gold/完整版本/CAS/回退 | course evaluation与原反馈/发布反例 |

从根运行 `py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request`。第一次先看missing→answered→replay→unknown，再沿每章的当前源码链接阅读。M35/M36将消费同一课程；此处不配置云端或激活新发布。下面保留已有机制入口，历史M01指南是补充阅读。

## 怎样学、怎样复验

第一轮读01–03、08–09，能指出DTO、唯一600、缺槽0工具、原receipt与事实回复。第二轮按04–07运行屏障实验，再回看主链的await/事务/shield。第三轮读10–14，用交错问题、UNKNOWN和原版本门禁深化。读不懂时先看当前行的输入/返回值/拥有者，不一次铺开全部适配器。

安装tools wheel后同样使用 `python -m deephelp_tools.learning.course <topic>`；具体topic见上表，`--help`列完整11项，没有live选项。实验只在终端输出结果，不写新的运行指针、预算或报告。每次CLI30秒上限，线程另有有限退出兜底。主进程网络守卫与受控stdio边界见01章，真实模型质量/PG持久性/云平台必须由对应显式probe验收。

三条主线练习贯穿课程：优惠缺槽→补订单号→事实，物流拒识，原消息重投；深化为优惠/券交错、取消收尾与UNKNOWN先对账。关键源码注释的阅读顺序见[source-navigation](source-navigation.md)。课程证明机制和命令可运行，不代替读者自测或把历史live记为本轮通过。

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
