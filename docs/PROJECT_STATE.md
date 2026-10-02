# 项目当前状态

更新：2026-10-02。只记录实现与实际验证；命令见 [ROADMAP](ROADMAP.md) / [LOCAL_SETUP](LOCAL_SETUP.md)。

| 范围 | 状态与证据 | 限制 |
|---|---|---|
| 工程 | Python3.14.7；根uv workspace、单一uv.lock；modules/deephelp-app | uv须满足根pyproject要求，本机启动器见LOCAL_SETUP |
| P00 | MySQL SELECT、Redis鉴权PING、Milvus元数据读取均PASS；报告 `.local/infra-health/client-health.json` | 只读连接验证，不是容量/业务检索/full复验；[历史交接](../handoffs/P00.md) |
| M00 | DESIGN_ACCEPTED；[交接](../handoffs/M00.md) | 设计基线，不是已实现业务系统 |
| M01 | IMPLEMENTED_OFFLINE_VERIFIED；[交接](../handoffs/M01.md) | health为骨架；converse为501/NOT_IMPLEMENTED；真实网关见M03 |
| M02 | IMPLEMENTED_OFFLINE_VERIFIED；[交接](../handoffs/M02.md) | `0.2.0-m02` 唯一类型、三类意图、36 条合成固定样本；无模型/云库/审批执行，非业务闭环 |
| M03 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M03.md) | chat/schema/tool/embed分别实测；1024维签名、有界缓存/预算/诊断；工具协议合成，不是MCP或业务闭环 |
| M04 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M04.md) | 完整原文/分段、Regex→API→可选强模型、证据/更正/规则候选；300不确定主意图，非业务闭环 |
| M05–M14、M16–M18 | NOT_IMPLEMENTED | 规格存在；按用户启动的特性顺序实施 |
| M15、M19 | HARDENING_NOT_IMPLEMENTED | 可后移，业务写工具前必须M15 |
| M20、M21 | OPTIONAL_NOT_IMPLEMENTED | 不阻塞客诉核心路线 |
| 模型/业务联调 | MODEL_GATEWAY_LIVE_VERIFIED；业务 NOT_RUN | 选定两模型四能力通过；真实千问+合成短文本，未运行M08或企业工具 |

M01已有客户端生命周期、deadline/预算/取消、错误/trace、fake Ports和三个异步实验。M02已原位扩展唯一类型，增加实体来源/更正、意图候选/策略、事实/工具证据、归属/参数/预算守卫和状态映射；目录有三类 actionable 意图，合成样本 reference/dev/regression=6/12/18，业务 fixture 有13订单、4券、1活动。缺订单演示为 CLARIFY/WAITING_SLOT/run SUCCEEDED/工具0。M03增加ChatPort/EmbeddingPort、严格schema/原生工具字段、调用/token/费用预留、错误分类、有界LRU和结构诊断；全零Embedding index按选定模型显式适配，normalization=l2、revision=null。未安装LangGraph，未连接真实业务库；converse仍为501骨架，真实网关通过独立受控入口验证。

M04新增兼容 DTO TextCleanResult / ExtractedEntity / RuleMatch / TextEntityResult。清洗只规范全半角ASCII及空白，原文、否定、金额/日期/数量、表情和尾部实体保留；Regex扫描完整原文，模型只补缺失/冲突字段。提取值必须匹配原文跨度、字段语义和白名单；多值不选最后一项，明确更正保留旧/新消息来源，模型不能覆盖已确认值。模型分段未完整覆盖或后段失败时，保留观察并澄清，不确认局部结果。三类版本化规则只提供候选；demand_type只描述话语功能。22条合成黄金样本及强模型端口内容验收通过，真实接口使用qwen3.8-flash/qwen3.8-max；不部署本地7B，不宣称原文推理时延。API未知/冲突保持未解析，预算/timeout/取消有界；converse仍为501。

最近全工程离线验证：Ruff检查/格式、mypy退出0；Python3.14.7下 **252 passed、1 skipped**，1207条pytest-asyncio policy API弃用警告；其中M04新增69项用例，样本/fixture和原M01/M02/M03消费者保持通过。integration使用合成ASGI/loopback HTTP，e2e验证组装/退出，不是业务或云端e2e。M02–M04没有迁移，内存消息冲突不等于MySQL唯一约束；审批等待类型/写工具禁用。CI配置存在，本机结果不冒充远端CI执行证据。

LOCAL_SETUP指向本机凭据、模型摘要和连接结果；调用前实时核对可调用性。Windows入口统一py -3.14 -m uv，解释器3.14.7、uv0.12.13；根workspace和单一锁保留。模型选用遵循[AGENTS](../AGENTS.md)的学习效果优先原则，允许使用候选池和账号余额；当前网关仍为固定模型配置，自动切换未实现，默认不开启live。短输出、请求大小与推理模式限制见[模型矩阵](MODEL_CAPABILITIES.md)，业务质量对照未执行。

M04实际探针、累计运行上限及原始报告留.local/m04；默认回归和演示离线。未调用业务库、MCP或P00 full，未启动M05；后续模块须由用户启动。

原始P00报告、旧安装提示、一次性生成工具和上游参考副本已移出当前工作树，保存在 `.local/archive/context-cleanup-20261002/` 和Git历史。运行配置、运维/验收工具、原PDF、M01源码/测试和模块规格保留；本次未启动下一M。

## 后续真实门禁

- 模型调用：核对可调用性与任务运行上限，验证实际内容；已验证模型见[能力矩阵](MODEL_CAPABILITIES.md)，不外推全部目录。
- M05：专用集合真实导入/检索/容量/索引；P00四维向量不证明语义效果。
- M15：MySQL持久saver兼容、审批并发、下游幂等/查询与崩溃恢复。

MySQL保存业务事实，Redis/Milvus是可重建投影，checkpoint不是账本。门禁按当前特性启用，不阻塞纯离线工作。
