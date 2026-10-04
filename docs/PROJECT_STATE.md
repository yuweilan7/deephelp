# 项目当前状态

更新：2026-10-04。只记录实现与实际验证；命令见 [ROADMAP](ROADMAP.md) / [LOCAL_SETUP](LOCAL_SETUP.md)。下表验收状态来自各特性的历史证据，不表示今日重跑。

| 范围 | 状态与证据 | 限制 |
|---|---|---|
| 工程 | Python3.14.7；根uv workspace、单一uv.lock；独立app/tools/services三包 | 运行分层及安装/资源边界已离线验证；本机启动器见LOCAL_SETUP |
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
| M19 | IMPLEMENTED_OFFLINE_VERIFIED；[交接](../handoffs/M19.md) | 运行分层、独立工具/demo安装、冻结数据字节和机制导航通过；模型0次，无新完整业务发布 |
| M20 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M20.md) | Business特性22项实测；独立Retrieval、原Port接入、同向量三路对照、断连/重启与跨两个MCP业务11项实测通过；个人合成数据、loopback运行 |
| M21 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M21.md) | OTel/跨两MCP/图/Saver、真实Tempo/Prometheus/私有Grafana、受保护run分页/深链接、独立重启与两类导出故障通过；应用未迁移 |
| M22 | NOT_IMPLEMENTED | 四个顺序前端特性，含MCP/schema与LangGraph/checkpoint教程；现有debug.html不是完整工作台 |
| M23 | NOT_IMPLEMENTED | 个人云应用部署、浏览器闭环、隔离恢复/回滚；现有云中间件不是应用已部署 |
| 模型/业务联调 | MVP_LIVE_VERIFIED；企业业务 NOT_RUN | 通用Chat改qwen3.8-max，schema/tool及M18真实内容通过；原Embedding签名保持；Rerank三候选补验通过、主链未接入 |

## 当前运行与验收边界

正式业务入口为 `python -m deephelp_app`；M01默认学习骨架保持501。运行代码、SOP/模板、策略/词典、demo事实与learning/probes/evaluation已分开，正式组装不导入学习/验收工具或读取dev/test。根Python3.14.7、uv workspace、单一锁、infra及MySQL事实源保持；Redis/Milvus可重建，checkpoint不是账本。

完整发布以MySQL单一active/previous及运行引用为准，绑定不可变资产、`runtime-v2`和独立`tooling_digest`。待审批/UNKNOWN/历史run与审核派生仍需保留工件；旧清单严格核对其绑定字节，不用于冒充当前包通过。M15已经接入批准/拒绝/撤销、唯一执行与UNKNOWN对账，默认只读；synthetic_rights仍为合成下游，不修改M20业务表。企业支付/下游、任意RUNNING重领及M22–M23未实现；M21观测平台已集成，DeepHelp应用仍本机运行。

2026-10-04路线规划后的M19交付：运行源码归domain/application/adapters/api/bootstrap/resources，工具/demo独立deephelp-tools；datasets与demo/data为单一数据源，wheel安装后读取工具包资源。该特性时业务仍为显式合成JSON后端，一个领域和引擎，没有学习/正式双份实现。全量924 passed/1 skipped及后增安装/发布59项通过，927项最终收集；两包各83文件逐字节一致，无工具/无Git检出的独立运行安装与追加工具stdio通过。该特性模型0次，无云检查、部署或新完整发布；旧清单不能套到新运行字节。

M20第1特性接入MySQL订单/券/活动、CAS维护与关系、不可变查询快照，独立Business MCP/维护事务复用原引擎。显式数据库模式采用实际initialize协议2025-11-25与双端守卫，Bearer和短期受保护委托独立于stdio临时key；正文/trace/session不授权，数据库失败关闭。该特性真实三类新记录、实际检索/金额/券/活动、更新后新消息/旧证据零调用重放、越权、并发版本冲突、独立服务/应用重启等22项PASS；数据为个人合成持久事实。该时点全量956 passed/1 skipped；最终网络协议/确定性31项通过、958项收集，三包构建及无tools独立安装/HTTP协议通过。

M20第2特性已接入独立Retrieval MCP与原Dense/Hybrid Port，复用原模型、签名、发布语料、K/weight与排序。服务固定允许主体/发布scope，双端回验schema/候选语义及签名预算回执；应用派发前持久预留，未知断连保留上界，重启不归零。两条固定查询使用同一次实际Embedding向量，Dense/BM25/Hybrid候选、证据、排序与分数一致，最大差异0；检索服务独立重启、跨Business/Retrieval MCP金额与券门槛、应用重启零调用重放及越权共11项PASS。流程视图记录实际Embedding/Milvus/三路动作及双端校验；私有记忆、事务、outbox/checkpoint仍在应用。

第2特性较广app/tools离线926 passed/1 skipped；最终services、M16、预算及运行边界组合113项通过，986项收集。Ruff/193文件格式、140源文件mypy、根锁及三包构建通过；wheel资源88/6/85文件逐字节一致。无tools、无Git检出的独立安装中70个app模块与4个services模块可导入，CLI及两个正式HTTP服务的SDK发现/检索profile握手通过；该安装检查不调用模型或云库。细节见[M20](../handoffs/M20.md)。未产生新完整发布、前端或应用部署；M20两项结束。

同日M19后源码核对时：ToolGateway已有正式SDK/stdio与双端守卫，当时业务来源为demo JSON且无独立HTTP服务；M20现已补上Business与Retrieval服务。approval.py确有LangGraph StateGraph/interrupt/resume，checkpoints.py确有自建MySQLSaver及pending writes，非官方MySQL插件；并不代表整条13段主链都是LangGraph。后续按此复用，不重建已有能力。用户提供4核8GB/低负载截图，仅是截图时点，不作本轮服务器容量/峰值测量。该次核对为规划时点；当前M21标准SDK及Tempo/Prometheus/私有Grafana已实测，未部署应用。

代表性历史证据：M17冻结548会话/1820消息、22业务情境；真实抽样29会话43消息×4组及17审批/6越权/9重放。运行边界特性真实HTTP 15/15，完整发布两版各14/14消息、11/11会话与23项守卫通过。数据同作者合成，不证明企业泛化或质量增益。必要明细在[M14](../handoffs/M14.md)、[M15](../handoffs/M15.md)、[M17](../handoffs/M17.md)、[M18](../handoffs/M18.md)，此处不复制逐轮过程。

按影响验收已实施：M03能力、M04实体样本/强端口、M17模式/完整会话可独立选择；完整四组显式启动。pytest收集前阻止远程DNS/TCP，保留本机HTTP/stdio；`--live`仅配置检查。报告区分实际usage、未知项、预留与费用估算。所有验收在特性分支，main仅合并、保存与同步。

M21两项已交付：标准Python OTel SDK1.45.0、Collector0.128.0、Tempo2.8.2、Prometheus3.4.2、Grafana12.0.2。原两MCP实际trace42 span及4个审批恢复Link可查询；受原身份/session保护的Tempo分页、Grafana实际帧/12面板查询、四组件独立重启持久及Collector/Tempo停止时金额173.45/唯一派发均通过。最终固定平台主链采用ReplayAssembly（模型API0），不证明新增质量；本轮Embedding2次用于真实检索关联。完整离线968 passed/1 skipped，最终观测组合88项、相邻M15/API/运行边界38项通过，后增例未冒称参加同一次全量。隔离观测栈cgroup当前合计361.9MiB、各容器峰值之和367.5MiB，RSS合计778.2MiB，主机可用5.75GiB；仅当前负载，无M23容量结论。详情见M21交接。M21结束，不自动启动M22/M23。

## 全项目清理与任务收尾

`feature/project-cleanup` 已在特性分支离线验收：18受控文件及6875原有忽略文件删除，旧重复PLAN/临时环境/渲染/构建/胶水脚本/迭代与等价行日志清除；占用从约1.896 GB降至0.724 GB。必要规格/学习资料/最终证据、875份本机保留来源与配置hash、96条账本文件引用保持。旧修订稿和暂停调度引用暂留，理由见[M18清理交接](../handoffs/M18.md#全项目清理与任务收尾)。

最终918 passed/1 skipped、919项收集；Ruff/154格式文件/110源文件mypy/根锁/构建通过，wheel147份包文件和安装边界通过。模型API 0次，运行内容与冻结资产字节不变；只读MySQL保护引用，不删云端数据。AGENTS与现有入口落实任务收尾、必要证据保留、成功后清除等价journal及原子临时文件；完成本特性后停止，不启动业务/部署，main不复验。

## 回归测试精简

`feature/test-suite-pruning` 删除M19重复摘要测试和M02逐条重复标注检查；无Git来源/运行字节绑定迁入runtime边界，并独立覆盖learning/probes/evaluation排除。M02整份36条语料校验保留，新增7个非法标注反例；M08的36个API场景及ID全部保留。M02只读fixture、M08样本加载及services独立凭据/检索资产准备整合，嵌套metadata仍逐用例隔离。M07/M08/M14、M10/M12的不同调用层、协议和组装路径保留；产品源码、配置、Prompt、冻结数据、运行资产、根锁、CI与收集前网络守卫未改。

测试文件45→45、测试函数455→453、展开用例986→958（app 765→766、tools 163→134、services 58→58）；收集精确差异38旧节点移除、10新节点加入，默认live配置跳过项仍为1。清理前完整离线985 passed/1 skipped、263.87秒；迁移后70项、删除后相关138项通过，10个内存缺陷全部被捕获；Ruff、193文件格式、140源文件mypy及测试文档路径检查通过。

最终完整离线为956 passed/1 failed/1 skipped、254.76秒，退出1，不能标为验收PASS或性能增益。失败为services的`test_disconnected_http_tool_is_classified_without_facts_or_demo_fallback`：loopback服务退出后SDK会话初始化抛出含`httpx2.ConnectError`的ExceptionGroup，未返回FAILED；主体未改，根因与准备整合的因果尚未确认，未顺带修复产品。2026-10-04用户知悉失败后明确授权直接推送/合并、删除过程副本且不再跑测试；按此交付保留已知未通过项，无重复测试、模型/云调用、构建或main复验。持续提速与维护工时收益未测量；失败定位信息继续保留本机。
