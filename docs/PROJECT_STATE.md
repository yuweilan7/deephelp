# 项目当前状态

更新：2026-10-06。只记录实现与实际验证；命令见 [ROADMAP](ROADMAP.md) / [LOCAL_SETUP](LOCAL_SETUP.md)。下表验收状态来自各特性的历史证据，不表示今日重跑。

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
| M21 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M21.md) | OTel/跨两MCP/图/Saver、真实Tempo/Prometheus/私有Grafana、受保护run分页/深链接及导出故障通过；平台由M23远端组合复用 |
| M22 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M22.md) | 四项学习页面已完成；M23在云端提供同源工作台，业务仍为个人合成持久资料 |
| M23 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M23.md) | Linux3.14.7三包/FastText、保签名路径绑定、新完整发布、浏览器四轮/同trace、独立重启、实际隔离恢复和安装回滚通过；私有SSH入口、短时低并发，不证明公网TLS/长期SLA/历史代码升级 |
| M25 | LIVE_VERIFIED；[交接](../handoffs/M25.md) | 两特性完成；新源码关系仓储统一PG，26表13846行原值迁移/续跑/冲突守卫、Windows/Linux仓储、实际dump→另库恢复与代表查询通过；M30已完成正式单写切换 |
| M26 | LIVE_VERIFIED；[交接](../handoffs/M26.md) | 两特性完成；官方3.1.2双平台独立进程/593旧checkpoint/1137待写原ID转换、正式装配/学习HTTP同图、8故障/7闭合/并发/取消互斥通过；130旧操作原计划/版本/79效果保持。源待审批1条缺绑定拒绝保留，4条UNKNOWN过期查询后保留；M30正式切换仍保留这些守卫 |
| M27 | LIVE_VERIFIED；[交接](../handoffs/M27.md) | 两特性完成；固定Langfuse/SDK、独立PG/CH/队列/SeaweedFS，双平台存入及同ID浏览器/API、取消/故障/71条读回、实际冷备→隔离恢复均通过；180.61秒15容器+三服务/索引训练/模型等待容量通过。低负载短窗口；业务接入见M28 |
| M28 | LIVE_VERIFIED；[交接](../handoffs/M28.md) | 两特性完成：原生SDK/官方callback、跨MCP与暂停恢复持久前序；实际业务13段、generation脱敏IO/usage、原工作台/分页/越权及停用故障重放通过；旧部署与Tempo历史保留M30 |
| M29 | LIVE_VERIFIED；[交接](../handoffs/M29.md) | 全部特性已交付：原历史29行映射/恢复及工作台0模型；不可变Prompt版本→本地hash工件→原完整发布，11会话14消息内容PASS；平台annotation经显式审核/CAS回流，实际v1→v2→v1与平台故障下原请求通过；两版本文本等价，不宣称质量提升 |
| M30 | LIVE_VERIFIED；[交接](../handoffs/M30.md) | 两特性完成：直接指标/新安装，最终源快照/官方历史转换/PG单写、新代码A/B各完整内容门禁及实际回滚、云浏览器四轮/同trace120观测/审批一效果、独立故障恢复、180秒容量及隔离全存储恢复通过；旧MySQL/Collector/Tempo及旧指标栈已停用且取消自启，原数据/冷备/bak保留。合成单机短窗口，不声明长期SLA |
| M31 | IMPLEMENTED_LIVE_VERIFIED；全部三项完成；[交接](../handoffs/M31.md) | 专用端口、同候选dev/test、真实会话/独立MCP/四路学习与新完整发布已验；N6/default/fail_closed、最终K3。test总体持平且一条退化，拒识6/6；正式默认关闭，开启版保留显式试用；不证明企业泛化 |
| M34 | IMPLEMENTED_LIVE_VERIFIED；全部两项完成；[交接](../handoffs/M34.md) | 固定原个人owner/自动HttpOnly会话/CSRF、删除登录退出；新精确发布/云安装、浏览器维护→补槽→事实/同run/重投、刷新/重启、CAS/外身份/原审批归属及旧版→新版恢复通过；后续学习体验交付见M32/M33/M35/M36各行 |
| M32 | IMPLEMENTED_OFFLINE_VERIFIED；全部三项完成；[交接](../handoffs/M32.md) | 唯一类型/会话/归属及适配器/装配/工具/服务归组；52测试文件按职责移动、独立断言保留；62无消费者过程文件已删除，源码导航可用；模型0次；随后由M35/M36交付新正式安装 |
| M33 | IMPLEMENTED_OFFLINE_VERIFIED；全部三项完成；[课程](learning/README.md) · [交接](../handoffs/M33.md) | 完整14章Java课程、11个安装可运行离线topic、原图UNKNOWN/反馈/发布实验及关键中文注释；屏障/取消/线程/子进程/共享候选/交错/证据门禁通过；模型0次；随后由M35/M36交付新正式安装，不代替用户学习或真实质量 |
| M35 | IMPLEMENTED_LIVE_VERIFIED；全部两项完成；[交接](../handoffs/M35.md) | Vue3/TS/Vite/Element Plus八任务入口、全部原能力迁移、唯一源码/前端锁及同源wheel；正式个人安装、14/14内容门禁、事实/重放/检索/记忆/观测、CAS、重启及M34→M35恢复通过；后续M36结果见下行 |
| M36 | IMPLEMENTED_LIVE_VERIFIED；全部三项完成；[交接](../handoffs/M36.md) | 原run确定性解释、同来源机制/观测与课程联动；八场景浏览器、14/14精确发布、正式安装/重启/M35→M36恢复通过；平台缺失/usage/CPU/排队按实未知，浏览0模型 |
| M37 | IMPLEMENTED_OFFLINE_AND_CLOUD_READ_VERIFIED；全部两项完成；[交接](../handoffs/M37.md) | 干净目录七个核心实验/七个审批反例、75项相关回归通过；正式包仍要求前端；当前入口/云原记录及可选配置边界已核验，新增模型0次，云安装保持M36；系统临时核验环境当时因自动审批拒绝暂留，已由M39核对后实际删除；原历史证据保持 |
| M38 | IMPLEMENTED_OFFLINE_VERIFIED；全部两项实现/验收完成；[交接](../handoffs/M38.md) | 主链及支撑路径中文契约/时序、全范围走查与现有导航完成；连接配置/SSH容量职责收口，旧诊断端口及评测回放兼容问题修复；最终1260 passed/2 skipped、infra 8项、三包内容与独立安装11课程通过；模型0次，新源码不等同于仍保留的M36云安装 |
| M39 | IMPLEMENTED_OFFLINE_VERIFIED；两项清理与保护核对完成；[交接](../handoffs/M39.md) | 删除完成M18/M39 PLAN及13487过程/临时文件（340929947字节）、2740空目录；1245保留hash不变，断连1项复验通过；模型0次，云未知用途对象保留 |
| M40 | IMPLEMENTED_LIVE_VERIFIED；全部三项完成；[交接](../handoffs/M40.md) | 默认single，显式订单/券角色/两源门槛；PG任务短事务/CAS/取消/只读回收；12条原主链内容对照/正式两HTTP MCP事实、新精确安装与M36→M40恢复通过；固定样本未证明质量收益，协作增加调用/等待 |
| M41 | IMPLEMENTED_LIVE_VERIFIED / LOCAL_CLOSEOUT_BLOCKED；三项能力验收/安装通过；[交接](../handoffs/M41.md) | 版本化订单/券Skill、只读组合、候选审核/完整发布；真实内容/页面/精确安装/恢复通过；本地临时产物清理被工具拒绝，整M收尾未完成。确定性选择，无模型选Skill质量收益结论 |
| M42 | 三项能力/精确云安装与恢复已验；LOCAL_CLOSEOUT_BLOCKED；[交接](../handoffs/M42.md) | complaints-v3六叶子、PG维护/CAS、真实HTTP MCP/600/澄清/事实、15例候选及6例正式内容、冻结对照、同run原生观测与旧新零调用恢复通过；F2七文件/两目录及已覆盖短PLAN删除被工具拒绝 |
| M43 | IMPLEMENTED_LIVE_VERIFIED；全部三项完成；[交接](../handoffs/M43.md) | 可视草稿/CAS/校验、原执行器预演/取消/持久重载、不可变审核与原完整内容发布门禁；实际浏览器闭环、修改分支及相邻PG/HTTP内容、同run Langfuse、精确云安装/M42↔M43恢复通过；浏览/历史0模型，35张非发布表恢复时行数/内容摘要不变 |
| M44 | IMPLEMENTED_LIVE_VERIFIED；全部三项完成；[交接](../handoffs/M44.md) | PG语料草稿/CAS/导入审核、不可变冻结/独立候选索引/同向量诊断、原反馈人审回流及完整发布；正式Hybrid不退化、固定test两版本8/8分类正确、六叶子及2未知业务通过；精确三包安装/重启/M43↔M44恢复通过，37张非发布表不变。BM25诊断MRR下降，无质量提升或客户泛化结论 |
| M45 | IMPLEMENTED_LIVE_VERIFIED；全部三项完成；[交接](../handoffs/M45.md) | 独立AI合成48会话86消息，train/dev/holdout 8/16/24，两位AI盲标与一轮第三方裁决；dev两路均15/16。事前审核/唯一holdout原版23/24、候选24/24，提升4.17个百分点、p=1，未达5个百分点/p≤0.05，REJECTED；该项当时保留M44，未证明客户泛化 |
| M46 | IMPLEMENTED_LIVE_VERIFIED；全部三项完成；[交接](../handoffs/M46.md) | PG可追溯历史/精确选择、当前冲突/CAS更正与遗忘；八场景真实内容/原回执零调用、精确三包安装与M44两向恢复；45表中43非发布表不变，旧schema严格停下；无自动向量召回或质量提升结论 |
| 模型/业务联调 | MVP_LIVE_VERIFIED；企业业务 NOT_RUN | 2026-10-05按用户长期选择固定通用Chat为qwen3.8-flash，强判仍qwen3.7-flash；新11会话14消息内容、云PG订单/同run Langfuse/零调用重放及配置恢复通过；原Embedding签名保持，Rerank主链已接但正式默认关闭 |

## 后续规格与实施边界

2026-10-05已交付M40–M46的后续能力规格：多Agent、Skills、业务目录扩展、可视化SOP、语料工作台、独立质量验证、跨会话记忆；能力/feature及直接依赖见[ROADMAP](ROADMAP.md#后续能力规格)。M40全部交付，M41三项能力已验收部署、本地清理尚有工具拒绝；M42三项能力已顺序验收部署，本地清理仍有工具拒绝。M43、M44各三个feature均已顺序验收交付，见[M44交接](../handoffs/M44.md)；M45三项已验并以拒绝候选闭环完成；M46三项已完成，合法历史来源/精确选择、冲突/更正/遗忘、真实生命周期/精确安装与M44两向恢复通过，见[M46交接](../handoffs/M46.md)。取消独立全量注释M；M19/M32–M39沉淀的职责、中文讲解、课程/前端学习与原运行观测、相关清理/文档同步已写成[每个feature的交付习惯](../AGENTS.md#每个feature的编码与交付习惯)。不重启已完成专项，不启动远期能力或调度。

## 当前运行与验收边界

以下云事实为2026-10-06本次M46正式PG/HTTP内容、新安装重启及M44↔M46两向恢复后的观察。精确安装来自干净源码d9fb43c325e8e59dbffda40b2db705e646a6b399，后续交付文档不改安装字节；原M44及更早有效安装/恢复资料保留。

| 当前运行项 | 已记录值/边界 |
|---|---|
| 正式云安装 | `m46-long-memory-v5`；运行根`/srv/deephelp-modern`，原活动PG事实库及PG18.6保持 |
| 业务/观测 | app、Business/Retrieval MCP、合成权益四服务；同run原Langfuse/官方图callback与直接指标 |
| 模型/检索 | 通用Chat `qwen3.8-flash`，归属/强判 `qwen3.7-flash`，原1024维Embedding；正式Hybrid复用M44独立21条语料/集合，Rerank未配置 |
| 当前channel | `m30-personal` revision48；active为M46精确记忆版，previous为M44审核语料版 |
| 浏览器教学 | 原事实/13段/解释/同run观测和作者入口；跨会话明确范围/精确引用、拒绝原因/版本审计及原run采用；浏览/历史0模型 |
| 仍未配置/未实现 | 自动跨session语义召回/个人画像没有实现；历史不送模型、不填当前槽位、不扩大工具/审批许可；catalog历史映射未配置、任意RUNNING重领及企业支付未实现 |
| 旧路线 | MySQL/Collector/Tempo与旧应用/指标已停用且取消自启；原数据/冷备/配置/受引用资产和有效旧安装保留 |

当前完整发布active：`1acb39608adbf3686f75e0df9cf9b302a86fb2876498b9aef285312a0fe9fa52`；previous：`3ebb539f7ba8bce8b9907127765c02a51959f462b877118803e705bee8a08c18`。runtime：`ac46b075a6af4715eb5824c1b65d090e58ad4d9b691482c3365511843fd3ef6d`，tooling：`a0204cb9c460aa76091db79f1732eccbca8cd5b8f42d478a0d08e128ec6c3a72`。完整清单绑定m46-explicit-v1/m46-memory-v1策略/schema和八记忆内容场景，复用M44冻结语料/作者人审、M43批准SOP与M41批准Skill。固定16分类/15业务行实际通过；最后仅记忆发布守卫修正时，逐文件核对模型/执行/DTO/UI未变，当前守卫重新验证原件并绑定独立复核链，没有重复模型请求。正式Hybrid与固定test分类保持，BM25诊断下降仍保留，不宣称记忆质量提升或客户泛化。

M46独立state/task_root在`artifacts-m46/.local/m46`，原Windows根映射到`artifacts-m46`。同一M46累计账本只在已关闭本机writer后移交一次，云端原应用拥有活动writer；原M44账本不覆写。正式新请求实际绑定当前manifest/记忆版本，PG当前金额/活动事实与历史参考分开；同run原Langfuse三条generation及零调用重放/读取通过。原四服务重启、M44↔M46恢复46→47→48通过，45张非系统PG表只有active/发布事件变化，其余43张含业务/作者/记忆/审批/UNKNOWN/checkpoint行数与hash不变，新旧模型用量不变。M44原记录可读，对M46新字段严格停止；回M46后原回执/墓碑/原观测可读。恢复与剩余适用边界见[部署说明](../infra/deployment/README.md#m46-跨会话记忆安装与恢复)及[M46交接](../handoffs/M46.md)。M44自身原验收/失败历史只见其[交接](../handoffs/M44.md)。

独立`m31-rerank-opt-in` revision1开启版为`449ff2b23a7b8d495782429786608bea5d01978b78acbaa2ec59f935d3dcc090`；仍属旧安装，不能直接用于M35及后续安装。M31同候选dev/test与主链真实内容通过，但test总体持平且一条退化，不宣称统一收益。M36历史14/14内容、八场景、安装/重启通过；M40新增六类两模式12条内容对照与正式两源PG/HTTP内容、两任务持久读取、150节点/6 generation同run观测及旧新恢复通过。M41新增六类single/审核快照内容、正式两源及浏览器分支，116节点/3 generation同run读取；完整33张业务/官方及旧checkpoint表恢复检查只见预期发布指针/审计变化，模型0。M37原PG事实181.23/13.45、资料version3、原13段与Langfuse85节点/2 generation/1082 tokens可读，新增模型0次。完整范围见各handoff，原观测里的调用不计为本轮新增调用。

## 当前限制与维护结果

数据是同作者合成/个人维护的学习资料；真实PG/MCP/模型协议不等于企业泛化。容量/恢复证据是固定单机低负载窗口，不是公网TLS、长期SLA、高并发或跨schema回退结论。缺失span/usage/CPU/排队按实未知；checkpoint、trace ID和相似度不提供授权或业务权威。

M38最终完整离线为1260 passed/2 skipped；两个跳过分别为Windows symlink权限与未开启live的配置诊断。历史test-suite-pruning的断连失败由M39当前原用例单独复验1 passed，旧异常日志已删除；不据此宣称持续提速或改写当时失败结果。M39删除2个完成PLAN及13487忽略/临时文件，1245份保留hash不变，核定临时对象无剩余拒绝项；云未知消费者对象保留。细节见[M39交接](../handoffs/M39.md)。

项目文档已按用途收敛：协作规则、状态、路线、运行、规格/证据各有唯一入口，完成PLAN不再保留，历史变更过程由handoff/Git追溯。删除旧RISKS登记，其仍有效的授权/UNKNOWN/预算/签名等防线已覆盖在AGENTS与CONTRACTS；删除独立M01异步说明，其CPU/进程与取消实验并入[资源课程](learning/07-resources.md#原m01异步与cpu补充实验)，原命令离线运行通过。旧模块的单特性停止、旧后端/登录/观测提示已修正为顺序交付与历史背景；Git问题自主修复规则只维护在AGENTS。此维护不更改源码、配置、服务、调度或验收状态；当前运行命令见[LOCAL_SETUP](LOCAL_SETUP.md)，课程见[learning](learning/README.md)。
