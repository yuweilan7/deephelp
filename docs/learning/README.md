# 从Java经验读懂DeepHelp

先读[从一条消息找到代码](source-navigation.md)，再按下表进入具体机制。

实际工作台首页有八个阅读场景；原run→机制页面→同一课程/源码/最小实验的完整阅读方法见[13章](13-observations.md#沿当前页面解释一次真实运行)。浏览解释不调用模型，正式结果与诊断缺口分别读取。

保留一套引擎。先用离线样本观察实际类型、决策、事实与trace，再由明确live入口运行真实模型；浏览文档、帮助、预览和pytest不会自动调用模型。异步基础从[await与Task](04-awaitables.md)开始，原M01的CPU/进程实验收在[资源课程](07-resources.md#原m01异步与cpu补充实验)。运行、工具和demo安装说明见[工具包](../../modules/deephelp-tools/README.md)。

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
| [09 Agent基础](09-agent-basics.md) | 模型建议、受控专业角色、版本目录与事实分支 | course request / collaboration / skills / catalog |
| [10 检索](10-retrieval.md) | 宽召回/重排/意图归并/最终确认 | course retrieval |
| [11 记忆](11-memory.md) | 交错问题/账本/outbox/投影 | course memory |
| [12 审批](12-approval.md) | interrupt/官方saver/UNKNOWN对账 | 原ApprovalService三种UNKNOWN测试 |
| [13 观测](13-observations.md) | receipt/run/trace/原证据/usage | course observations |
| [14 评测发布](14-evaluation-release.md) | 留出/gold/完整版本/CAS/回退 | course evaluation与原反馈/发布反例 |

从根运行 `py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request`。第一次先看missing→answered→replay→unknown，再沿每章的当前源码链接阅读。云工作台的机制页面复用同一课程和源码链接。下面保留已有机制入口，历史M01指南是补充阅读。

## 本地核心最短路径

新克隆先按[01章](01-start.md#从仓库根运行)准备Python3.14.7和根锁定依赖；无需旧`.local`、云凭据、隧道、Node或中间件服务。每次只选正在学习的topic，不要求一次跑完14章。下面的`course <topic>`均展开为`py -3.14 -m uv run --locked python -m deephelp_tools.learning.course <topic>`，已准备依赖后可将`uv run --locked`改为`uv run --offline --locked`。

| 次序 | 执行与阅读 | 先解释清楚的结果 |
|---|---|---|
| 1 | `course request`，读[01](01-start.md)/[08](08-message-path.md)/[09](09-agent-basics.md) | 缺order_id时0工具；补000031沿原question；99.90/10.00来自事实及证据；同message返回原run且四种计数0；物流HANDOFF |
| 2 | `course memory`，读[11](11-memory.md) | 优惠与券属于两个question；000031只补优惠，券问题仍保留000042；message/question/run分别对应消息、问题、执行 |
| 3 | `course retrieval`，读[10](10-retrieval.md) | 一次固定向量、相同三候选、不同排名、每意图最佳证据后K=2；候选排序不等于600最终判断 |
| 4 | 按需要选`course awaitables/coordination/cancellation/resources`，读[04](04-awaitables.md)–[07](07-resources.md) | 每个topic单独执行；Future由谁完成，顺序峰值1/并发2，TaskGroup先收回子任务，取消后finally/线程/子进程由谁关闭 |
| 5 | 读[12](12-approval.md#最小可运行unknown实验)，运行下方原审批反例 | UNKNOWN先query；已成功不再execute，明确ABSENT才派发，不可用仍UNKNOWN；未批准不能恢复 |

```powershell
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/unit/approval/test_m15_approval.py -k "unknown_queries_before_any_redispatch or resume_without_approval_never_calls_downstream" -v
```

预期7个参数化场景通过，其中UNKNOWN三种、未批准四种。实验沿用生产Conversation/HybridRetriever/ApprovalService与正式SDK或LangGraph；模型响应、向量和业务资料固定合成，账本及saver在内存。输出中的端口动作计数不是远程API用量；这些结果证明机制，PG跨进程持久性与模型质量按各自真实验收判断。修改Python后重跑对应topic或原测试，沿每章源码位置观察受影响的状态关系。

## 怎样学、怎样复验

跨会话能力从[第11章两会话实验](11-memory.md#两会话长期记忆实验m46)进入，运行`course long-memory`观察明确来源/精确版本、当前缺槽不继承历史、终态不重开与原receipt零调用重放。它复用同一Conversation，不另建教学记忆引擎。

第一轮读01–03、08–09，能指出DTO、唯一600、缺槽0工具、原receipt与事实回复。第二轮按04–07运行屏障实验，再回看主链的await/事务/shield。第三轮读10–14，用交错问题、UNKNOWN和原版本门禁深化。读不懂时先看当前行的输入/返回值/拥有者，不一次铺开全部适配器。

安装tools wheel后同样使用 `python -m deephelp_tools.learning.course <topic>`；具体topic见上表，`--help`列当前主题，没有live选项。实验只在终端输出结果，不写新的运行指针、预算或报告。每次CLI30秒上限，线程另有有限退出兜底。主进程网络守卫与受控stdio边界见01章，真实模型质量/PG持久性/云平台必须由对应显式probe验收。

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

真实执行的边界是明确启动live探针或配置正式serve。模型执行方式、业务数据源与运行地点各自选择；有Key或位于服务器不会自动联网。Business/Retrieval服务的运行、schema、委托、预算回执及维护学习见[服务包](../../modules/deephelp-services/README.md)。当前观测为原生Langfuse，旧Tempo路线仅保留历史资料。整个13段主链不宣称全部由LangGraph实现；课程/固定回放不替代真实质量验收或读者自测。

## 云端完整教学路径

按[当前云入口](../LOCAL_SETUP.md#当前云端个人入口)启动personal隧道并访问18080。先刷新持久历史，选自己的已有原run，读取事实/不可变证据/13段与解释，再沿观测、记忆、检索、工具和评测页面对照本课程。服务端完整workbench使用同一核心，不要求本地也有全部组件；普通阅读0模型，页面内明确执行的真实动作才走原预算。

云端数据是可维护合成资料，持久协议和模型为真实执行；原run诊断、历史版本、固定本地回放分别理解。个人身份不能接管独立synthetic审批，未配置平台历史映射保持实际空态；已验范围/当前只读观察见[M36](../../handoffs/M36.md)/[M37](../../handoffs/M37.md)，当前安装见STATE。基础业务serve和完整workbench、离线course与历史恢复的选择统一见LOCAL_SETUP。


M40专业Agent的任务上下文、独立决策/证据回收与Java边界见[09章新增委托实验](09-agent-basics.md#受控专业-agent委托m40)，命令topic为collaboration，默认离线/远程模型0次。
