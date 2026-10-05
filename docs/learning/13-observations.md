# 13｜调试、Langfuse、指标与原证据

[上一章](12-approval.md) · [课程入口](README.md) · [下一章](14-evaluation-release.md)

## 业务问题与目标

同一run在页面、PG和Langfuse里有什么关系？一次回复为什么慢，usage空白是不是0？目标是定位原输入/结果/证据，并正确表达未知与耗时。前置为四个ID和异步等待。

## 三种记录的职责

receipt是账本原接收/执行结果，回答“这条消息是否已执行、哪些事实已保存”；run串联一次业务执行；trace/span是诊断观测，解释阶段、调用与等待。trace不授权读数据，平台缺数据也不撤销receipt。

Langfuse的generation承接实际model.attempt，记录允许脱敏的输入/结构输出、版本、实际usage和状态；不会把隐藏推理公开。跨MCP沿可信元数据传递关联，平台读回仍先校验主体/session/run。审批pause与新进程resume是不同trace，用持久前序关联，不把十分钟人工等待说成连续运行。

指标按有限标签汇总请求/耗时/错误/资源，不存每条原文。父span包住子span，耗时不能简单相加：父100ms、内部两个串行子段60/20ms，总请求仍100ms；子段并发时相加也会重复覆盖。统计慢点需区分排队/网络/执行/存储，不能只看“哪个名字长”。Java链路追踪可类比关联，证据/授权仍由业务代码保证。

## 最小脱敏与同源投影实验

离线、合成DebugSnapshot、模型0次；不查询平台：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course observations
```

预期intent/turns/flow的run_id均为synthetic-run，订单号变稳定id_标识，正文金额99.90仍在，API key/隐藏推理不输出。flow.budget.tokens为null、token_upper_bound为2000；actual未知不能填0，预留上界不能当实际消费。incomplete含native_observations_not_loaded，otel_trace_id为null，不伪造平台已存入。

读[observations_demo](../../modules/deephelp-tools/src/deephelp_tools/learning/course/evidence.py)：先把合成数据送sanitize，断言敏感字段红acted与标识替换；创建同一DebugSnapshot；project只按三个视图选择字段，不重新执行模型。approval_recovery的旧协议字符串在没有approval字段时表示本快照无恢复数据，不能据此判断整个项目没实现M15。

## 沿源码找一次实际记录

从[diagnostics.capture_debug](../../modules/deephelp-app/src/deephelp_app/application/conversation/diagnostics.py)看本次输入、文本、意图、工具、retrieval和budget怎样放入同一snapshot；[debug.sanitize/project](../../modules/deephelp-app/src/deephelp_app/application/debug.py)保住脱敏与同源视图。span offset先指原文，替换标识后的展示长度不同，不能在脱敏文字按旧offset切片。

[native_observation.model_io/register_run/graph_callbacks](../../modules/deephelp-app/src/deephelp_app/adapters/observability/native_observation.py)捕获网关允许的IO、持久关联和每次图调用callback；[langfuse_query](../../modules/deephelp-app/src/deephelp_app/adapters/observability/langfuse_query.py)读取登记观测、分页和累计大小守卫。模型请求/返回对象不因脱敏被改写。

在现有工作台授权历史中选原run，再查flow里的tool_call_ids/evidence与Langfuse generation的版本/usage。普通浏览不重新发送原输入。平台不可用应明确显示诊断缺失；旧Tempo历史标legacy，不能声称已经导入Langfuse。历史真实平台证据见[M28交接](../../handoffs/M28.md)，本课程不重验平台。

## 常见误区与反例

- 实际API usage、预算预留上界、费用估算是三个量；reasoning已计入output时不能再相加。
- span数量不等于调用数；模型适配重试、stage、SDK callback各有观测用途。
- trace ID不是读取权限，不能直接按任意ID访问别人的数据或用平台日志代替原账本。

## 练习与答案

父span100ms、两子span60/20ms，为什么不能报180ms？usage缺失却预留2000，实际tokens是多少？

答案：父段覆盖子段，重复求和会重复计时；实际未知，保留2000预留等待可信结算/对账。诊断输出能解释结构，不能为未知供应商用量补零。
