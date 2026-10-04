# 项目当前状态

更新：2026-10-04。只记录实现与实际验证；命令见 [ROADMAP](ROADMAP.md) / [LOCAL_SETUP](LOCAL_SETUP.md)。下表验收状态来自各特性的历史证据，不表示今日重跑。

| 范围 | 状态与证据 | 限制 |
|---|---|---|
| 工程 | Python3.14.7；根uv workspace、单一uv.lock；modules/deephelp-app | uv须满足根pyproject要求，本机启动器见LOCAL_SETUP |
| P00 | MySQL SELECT、Redis鉴权PING、Milvus元数据读取均PASS；报告 `.local/infra-health/client-health.json` | 只读连接验证，不是容量/业务检索/full复验；[历史交接](../handoffs/P00.md) |
| 学习环境权限/API | LIVE_VERIFIED；[权限及API交接](../handoffs/P00.md#学习环境权限与api调整) | 三个中间件应用账号全权限；Redis实际重启后复验、模型六项内容验证；未跑P00 full |
| M00 | DESIGN_ACCEPTED；[交接](../handoffs/M00.md) | 设计基线，不是已实现业务系统 |
| M01 | IMPLEMENTED_OFFLINE_VERIFIED；[交接](../handoffs/M01.md) | 默认离线入口保持501；业务入口见M08，真实网关见M03 |
| M02 | IMPLEMENTED_OFFLINE_VERIFIED；[交接](../handoffs/M02.md) | `0.2.0-m02` 唯一类型、三类意图、36 条合成固定样本；无模型/云库/审批执行，非业务闭环 |
| M03 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M03.md) | chat/schema/tool/embed分别实测；1024维签名、有界缓存/预算/诊断；工具协议合成，不是MCP或业务闭环 |
| M04 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M04.md) | 完整原文/分段、Regex→API→可选强模型、证据/更正/规则候选；300不确定主意图，非业务闭环 |
| M05 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M05.md) | 版本化合成语料、真实1024维Embedding/Milvus、Dense候选/独立dev评测；不是最终分类或业务闭环 |
| M06 | IMPLEMENTED_LIVE_VERIFIED | 本机正式SDK stdio发现/调用、两个合成只读工具、鉴权/取消/退出；[交接](../handoffs/M06.md)；非企业下游或业务闭环 |
| M07 | IMPLEMENTED_LIVE_VERIFIED | 三份版本化SOP、受控执行器、真实模型+stdio内容/ledger；[交接](../handoffs/M07.md)；M08复用执行端口 |
| M08 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M08.md) | 三类单消息闭环、MySQL唯一接收/终态、真实模型/Milvus/MCP、13段trace；业务数据合成 |
| M09 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M09.md) | 原生中文BM25/Dense/融合对照、600接入；12条索引、18 dev、24 test合成样本，融合未全面胜出 |
| M10 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M10.md) | 显式多问题续接、生命周期、MySQL事实/outbox、Redis/Milvus可重建；自动归属留M11 |
| M11 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M11.md) | 独立自动归属/更正/澄清、事件图及MySQL/outbox；完整主流程接入留M12 |
| M12 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M12.md) | 同一13段主链自动归属、四层级联及逐请求计数；校准/业务均为小合成组 |
| M13 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M13.md) | Windows真实FastText训练/量化及原600可选兜底；80条小合成组，默认关闭 |
| M14 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M14.md#业务覆盖扩量) | 三流程治理/原19边界回归；新增22个实际业务情境全部真实通过，17正常结果+5记录/归属边界；批准后合成变更由M15接通 |
| M15 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M15.md) | MySQL持久checkpoint、明确审批/唯一领取/UNKNOWN查询对账；8进程/通信故障矩阵及真实模型HTTP合成效果通过，非企业支付/通用RUNNING重领 |
| M16 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M16.md) | 事实模板/受限语气、同源三视图与脱敏轮转；M15已接审批/恢复历史 |
| M17 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M17.md#完整业务规模扩量) | 548不同会话/1820消息、22实际业务情境离线全量；真实29会话43消息×4组及17审批/6越权/9重放组合通过；全部同作者合成 |
| M18 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M18.md) | 审核回流与完整ReleaseManifest/MySQL单一active已实现；运行绑定、审批资产保留、并发回退及进程中断真实通过；不提供每日自动训练/隐式文件删除 |
| M19 | PLANNED_NOT_IMPLEMENTED | 工程分层/工具独立安装/离线学习导航；[短计划](../planning/M19/PLAN.md) |
| M20 | NOT_IMPLEMENTED | 持久业务/维护与独立Business MCP，再独立Retrieval MCP；当前业务工具仍查询demo JSON |
| M21 | NOT_IMPLEMENTED | OTel/跨MCP/图与Saver观测，以及Collector/Tempo/Prometheus/Grafana；现有13段/三视图不代表新目标完成 |
| M22 | NOT_IMPLEMENTED | 四个顺序前端特性，含MCP/schema与LangGraph/checkpoint教程；现有debug.html不是完整工作台 |
| M23 | NOT_IMPLEMENTED | 个人云应用部署、浏览器闭环、隔离恢复/回滚；现有云中间件不是应用已部署 |
| 模型/业务联调 | MVP_LIVE_VERIFIED；企业业务 NOT_RUN | 通用Chat改qwen3.8-max，schema/tool及M18真实内容通过；原Embedding签名保持；Rerank三候选补验通过、主链未接入 |

## 当前运行与验收边界

正式业务入口为 `python -m deephelp_app`；M01默认学习骨架保持501。运行代码、SOP/模板、策略/词典、demo事实与learning/probes/evaluation已分开，正式组装不导入学习/验收工具或读取dev/test。根Python3.14.7、uv workspace、单一锁、infra及MySQL事实源保持；Redis/Milvus可重建，checkpoint不是账本。

完整发布以MySQL单一active/previous及运行引用为准，绑定不可变资产、`runtime-v2`和独立`tooling_digest`。待审批/UNKNOWN/历史run与审核派生仍需保留工件；旧清单严格核对其绑定字节，不用于冒充当前包通过。M15已经接入批准/拒绝/撤销、唯一执行与UNKNOWN对账，默认只读；synthetic_rights仍为合成下游，企业支付/下游、任意RUNNING重领、M19–M23未实现。

2026-10-04按用户要求替换未实施的旧M19–M21，并新增M22/M23规格；当前只完成路线规划。业务运行与工具已分目录，但learning/probes/evaluation/demo仍位于应用包，平铺源码的进一步职责分层尚未实施。模型执行方式、业务数据源、部署位置将独立配置；不另建一套学习/正式引擎。本轮没有运行模型、云检查、全量pytest或部署，不产生新的业务/质量/容量验收结论。

同日追加只读源码核对：ToolGateway使用正式MCP SDK/stdio子进程，input/output schema与双端守卫已有；合成的是demo业务来源，独立HTTP服务未实现。approval.py确有LangGraph StateGraph/interrupt/resume，checkpoints.py确有自建MySQLSaver及pending writes，非官方MySQL插件；并不代表整条13段主链都是LangGraph。新计划按此复用，不重建已有能力。用户提供4核8GB/低负载截图，仅是截图时点，不作本轮服务器容量/峰值测量。选定观测框架和服务拆分仍为规划，未安装或调用云端。

代表性历史证据：M17冻结548会话/1820消息、22业务情境；真实抽样29会话43消息×4组及17审批/6越权/9重放。运行边界特性真实HTTP 15/15，完整发布两版各14/14消息、11/11会话与23项守卫通过。数据同作者合成，不证明企业泛化或质量增益。必要明细在[M14](../handoffs/M14.md)、[M15](../handoffs/M15.md)、[M17](../handoffs/M17.md)、[M18](../handoffs/M18.md)，此处不复制逐轮过程。

按影响验收已实施：M03能力、M04实体样本/强端口、M17模式/完整会话可独立选择；完整四组显式启动。pytest收集前阻止远程DNS/TCP，保留本机HTTP/stdio；`--live`仅配置检查。报告区分实际usage、未知项、预留与费用估算。所有验收在特性分支，main仅合并、保存与同步。

## 全项目清理与任务收尾

`feature/project-cleanup` 已在特性分支离线验收：18受控文件及6875原有忽略文件删除，旧重复PLAN/临时环境/渲染/构建/胶水脚本/迭代与等价行日志清除；占用从约1.896 GB降至0.724 GB。必要规格/学习资料/最终证据、875份本机保留来源与配置hash、96条账本文件引用保持。旧修订稿和暂停调度引用暂留，理由见[M18清理交接](../handoffs/M18.md#全项目清理与任务收尾)。

最终918 passed/1 skipped、919项收集；Ruff/154格式文件/110源文件mypy/根锁/构建通过，wheel147份包文件和安装边界通过。模型API 0次，运行内容与冻结资产字节不变；只读MySQL保护引用，不删云端数据。AGENTS与现有入口落实任务收尾、必要证据保留、成功后清除等价journal及原子临时文件；完成本特性后停止，不启动业务/部署，main不复验。
