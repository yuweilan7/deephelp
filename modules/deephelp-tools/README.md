# DeepHelp 独立工具包

关系事实已统一PostgreSQL，M26审批使用官方保存器；启动前按[PG入口](../../infra/postgres/README.md)设置显式连接并执行migrate。正式云安装与源码分别见[PROJECT_STATE](../../docs/PROJECT_STATE.md)；旧完整发布继续绑定其原安装字节。

本包依赖 `deephelp-app` 的唯一领域类型与会话引擎，提供显式学习、demo、探针、冻结评测、训练及发布准备。运行包没有反向依赖。根开发环境通过唯一uv锁安装三个workspace包，独立机器可只安装app wheel；需要演示/学习时再安装tools及其依赖。

`learning/` 是fake、回放与故障实验；`demo/` 是正式MCP SDK的合成业务stdio服务及合成权益HTTP服务；`probes/` 是受控显式验收；`evaluation/` 是冻结集审计、训练、人工审核与发布准备。M20独立Business服务使用PostgreSQL持久资料；本包demo事实仍来自JSON。

evaluation按retrieval/quality/feedback/releases/migration/platform归组；probes按models/retrieval/protocol/conversation/approval/releases/platform/migration归组。learning/workbench组合工作台路由，replay/faults/fakes分别提供回放、故障与替代端口。原命令模块仍转调同一个main实现，内部import和patch使用职责路径。单元测试在tests/unit的对应目录，跨组件场景在tests/integration；调用者、资源拥有者与失败处理见[源码导航](../../docs/learning/source-navigation.md)。

从仓库根运行以下入口，帮助、预览和审计均不调用真实模型：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.experiments
py -3.14 -m uv run --locked python -m deephelp_tools.learning.replay.event_cli --help
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.retrieval.dense_cli preview
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.quality.evaluation_cli audit
py -3.14 -m uv run --locked python -m deephelp_tools.probes.models.live_probe --help
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.releases.release_cli --help
```

业务运行使用app的正式CLI，显式给出 `--mcp-module deephelp_tools.demo.mcp_server`；Hybrid另给出 `--corpus demo/data/m09_corpus.jsonl` 或实际发布语料。客户端保留工具白名单、可信主体、签名、输入/输出schema、共享预算、超时与取消守卫。`DemoAssembly`/`DemoToolGateway`只在本包明确装配demo，复用运行实现。

完整发布的 `prepare/validate` 位于 `deephelp_tools.evaluation.releases.release_cli`，`register/activate/rollback/status/references/retire/serve` 位于 `deephelp_app.bootstrap.commands.release_cli`。准备同时绑定运行字节和工具/输入指纹；serve无需安装评测工具，也不重新读取冻结集。

仓库中冻结数据唯一来源为[ datasets ](../../datasets/README.md)，demo事实唯一来源为[demo/data](../../demo/README.md)。构建tools wheel时将同一份字节纳入包资源，安装后不依赖仓库路径。学习数据放在本包resources/learning。工作区中的路径与安装wheel中的资源选择顺序详见assets.py。

学习路线见[Java课程](../../docs/learning/README.md)，安装后可执行 `python -m deephelp_tools.learning.course request`（types/syntax同入口）。课程在事件循环初始化后封住业务网络，复用原13段用例与受控stdio合成服务；不加载云配置。各模块既有运行与验收参数见[应用README](../deephelp-app/README.md)。pytest收集前守卫禁止远程DNS/TCP，允许loopback/受控stdio；不是任意子进程的网络沙箱。真实模型由明确live入口及共享预算控制。

## 浏览器学习扩展

安装tools后，以 `python -m deephelp_tools.learning.workbench.app serve` 代替基本app入口，其他业务/检索/身份/预算参数完全复用[主工作台启动](../deephelp-app/README.md#m22浏览器工作台)。基础app只显示已装配页面；浏览/固定协议实验不请求模型。

### 评测与飞轮目录

将[目录模板](learning-catalog.example.json)复制到自己的`.local/learning/catalog.json`，按实际已有报告/资产修改映射；移除不存在的条目。路径由服务器配置，浏览器只提交映射ID、候选ID或任务ID。最多16份报告/16个资产；单份浏览报告上限8MB，完整大报告使用已有汇总索引。模板没有凭据正文；`auth`指向原M18验证所需的专用合成身份配置，`redaction_key`沿用现有稳定32字节key，不能重置。`budget_state`与应用`--budget-state`使用同一累计文件；真实构建/验证复用已经打开的共享预算writer。

```powershell
$env:DEEPHELP_LEARNING_CATALOG = '.local/learning/catalog.json'
# 随后使用上面的 tools.learning.workbench serve 与原业务/检索/身份/预算参数
```

只有`owner`这一明确配置的个人学习主体可浏览该本机管理目录；其他可信身份仍只使用自己的业务工作台，目录读写404。全局审核/完整版本来自当前PostgreSQL，不另建事实源。页内主链采集仅接受已完成的专用`m18-collect`合成run，冻结评测输入不能回收为训练；内容生成沿用原[飞轮入口](../deephelp-app/README.md#m18-审核后的反馈闭环)。审核复用原revision守卫、留出隔离和三路队列，浏览器只能明确人工审核，不能伪造确定性证据注册者。

进入页面先读取计划与共享剩余预约数。冻结审计和四路离线回归为0远程模型；离线计数中的模型适配动作不等于远程API调用。构建基线12条语料、最多10个批准来源，默认每8条一批，预计2–3次Embedding（重试受原门禁）；FastText只在相应路由获批时训练。验证使用原M17/M18固定11会话/14消息、真实服务/模型，预计1–113次远程调用，实际依规则/检索/SOP路径；token范围是计划估计，API usage和预算预留另计，推理沿供应商配置，不关闭或裁剪有效上下文。预算不足会停止并保留诊断；已知不足以完成构建批次时在启动前拒绝。

显式任务为`audit/offline/build/validate/prepare/platform_upload/platform_candidate`，不接收命令、模型配置或任意路径；同一进程一个重任务、1800秒总上限、最多3次尝试、最多100份持久任务记录。取消等待原适配器退出再释放槽位；硬中断重启标`INTERRUPTED`，仅显式恢复才另建不可变尝试目录。已经完成或未知提交的资产/验证/版本登记先对账，原M18队列lease/尝试仍有效，不盲重放。任务`SUCCEEDED`只表示入口返回；`evidence_status`才是实际PASS/REJECTED/PREPARED/READY。任务目录保留状态和仍需恢复/浏览的产物，不作临时目录清空。

候选不修改active。`prepare`复用`prepare_manifest`及`ReleaseStore.register`，完整发布和回退复用`ReleaseStore.switch`、目标hash与revision CAS；回退还绑定当前previous。代码/工具/供应商/SOP/审核或绑定资产失效时旧报告PASS也不可发布。页面不自动执行训练、全量live或切换版本；浏览与审核本身不证明模型质量。

学习页按原operation/session/run读取本主体的官方checkpoint和实际审批图；显式配置合成权益服务并使用专用演示身份后，可创建固定练习、批准/拒绝/撤销及恢复同一原操作。普通消息仍不能批准旧操作，UNKNOWN先对账；synthetic_rights不改M20资料、不表示真实退款。页面不允许直接写checkpoint，也不暴露任意MCP、SQL或文件接口。旧reader仅供迁移资格验证，正式app不包含旧自建codec；转换与历史逐状态恢复见[M26交接](../../handoffs/M26.md)。

扩展还提供零模型实体规则练习、同一原run的意图/归属视图、显式同向量三路检索与只读记忆投影。三路查询使用当前Retrieval服务发布profile及原共享预算，预计一次Embedding、缓存命中可为0；不改变训练资产/门限。原文span在debug脱敏前定位，原始内容从授权历史核对。运行观测先校验已保存run的主体与session，再读取已登记的Langfuse观测；显示来源、实际generation用量、版本及暂停/恢复前序，管理员平台独立登录。未登记原生观测的历史明确标为legacy-tempo，保留[M21历史入口](../../infra/observability/README.md#平台与访问)。平台故障明确显示，不重跑业务。

## M28 原生观测验收入口

先按[本机环境](../../docs/LOCAL_SETUP.md)设置专用PG与原生观测私有配置，并检查已有隧道；
两MCP继承相同环境。根目录使用`py -3.14 -m uv run --locked --env-file .env.local python`前缀。
协议/官方图首试验`proof`为模型0次；`business`显式发送所选固定订单/优惠券消息，
复用现有模型配置、Embedding签名与实际PG事实。先建立本轮累计预算文件，参数按任务选择，
失败修复沿用原文件，不重置预算。输出必须新路径；凭据、报告与trace诊断保存在`.local`。

```powershell
$env:DEEPHELP_POSTGRES_CONFIG = (Resolve-Path '.local/m28/facts.json').Path
$env:DEEPHELP_LANGFUSE_CONFIG = (Resolve-Path '.local/m28/native.json').Path
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.probes.platform.native_observation_probe --live --mode proof --output .local/m28/proof-new.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.probes.platform.native_observation_probe --live --mode business --sample all --fault-checks --output .local/m28/business-new.json
```

`--sample order|coupon`只运行该样本。`--source <已有business报告>`按原消息ID重投并核对原回执，
模型新增调用必须为0；页面浏览与故障重放也不调用模型。
`--browser-control <临时路径>`在读回后保留服务最多5分钟，打印本机URL和已授权run/session，
人工或浏览器验收同一运行后将该文件写为`PASS`；它只登记页面检查，不能代替内容/API断言。
`--fault-checks`另开停用导出/不可用端点的应用进程，原预算锁仅在本入口拥有的进程退出后回收。
报告区分本轮实际模型调用与读回generation数；同一历史generation不会因重放再计费。
实际交付与适用范围见[M28交接](../../handoffs/M28.md)。


## M29 历史评测映射

平台使用锁定的v4原生Experiment。只接受原冻结集/manifest与报告来源一致的完整会话子集；不运行评测器、模型或业务。原逐行检查/失败、原报告门禁、空分母、未启用能力以及全部审批/越权/effects计数保持。原结果以JSON文本保存，避免平台数字序列化改变浮点精度。历史导入不证明当前模型质量或企业泛化。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.platform.langfuse_cli --live --config .local/m28/native.json --report .local/m17-scale/live-feature-final.json --data datasets/m17_scale_cases.json --manifest datasets/m17_scale_manifest.json --case scale-discount-applied-interleaved-hints --case scale-discount-ineligible-auto-fill --mode hybrid --state .local/m29/import-state.json
py -3.14 -m uv run --locked python -m deephelp_tools.probes.releases.langfuse_engineering_probe --live --config .local/m28/native.json --report .local/m17-scale/live-feature-final.json --data datasets/m17_scale_cases.json --manifest datasets/m17_scale_manifest.json --case scale-discount-applied-interleaved-hints --case scale-discount-ineligible-auto-fill --mode hybrid --state-root .local/tmp/m29-proof-new --output .local/m29/proof-new.json
```

导入器的状态文件与OS锁绑定来源/选择/平台project；重投相同命令读回相同Dataset item、Experiment observation和Score ID后才记COMPLETE。单次最多20会话/100行，每请求最多3次、5秒；每行读回60秒、导入600秒。probe需新状态目录/新报告，模拟实际不可用端点与取消，不新执行业务。

M22受控catalog可增加`platform_config`（私有配置路径）与`imports`映射；每个条目含`report/data/manifest/cases/modes`，最多16个。owner在原评测页选择条目、明确确认后启动`platform_upload`；网页不能传路径、key或命令。工作台独立子进程拥有SDK队列，660秒外层上限，取消确认子进程退出后保留部分状态，原串行任务与最多3次显式恢复保持。只读状态不初始化SDK；其他主体所有入口404。已完成任务、最终状态和验收报告保留，临时诊断可在核对后清理。

实际来源与限制见[M29交接](../../handoffs/M29.md)。

## M29 Prompt版本与标注审核

`langfuse_prompt_review create`将现有M12主意图兜底system文本及静态契约保存为平台版本；不改变模型或schema。`export`只接受确切正整数版本，不读取latest/production label作为发布文本。原输入JSON完整保留当前文本、已确认事件上下文与检索结果；模板引擎为none，无插值变量，文本不能包含平台模板占位符。模型/参数/schema/tools约束随本地工件一起核对。首次创建版本为非幂等请求，网络结果未知时先查询平台版本，不自动再POST。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.feedback.langfuse_prompt_review create --live --config .local/m28/native.json
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.feedback.langfuse_prompt_review export --live --config .local/m28/native.json --name deephelp-intent-fallback --version 1 --output .local/m29/prompt-v1.json
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.feedback.langfuse_prompt_review score-config --live --config .local/m28/native.json
```

导出的`m29-prompt-v1`工件具有name/version/text/contract/request_digest，路径使用OS锁且不可覆盖。模型内容验证使用原入口的`--prompt-artifact`，准备完整版本同时传该工件及本次验证报告；相同最终请求可复用仍适用的内容证据，版本/hash仍分别绑定。不同文本、契约、runtime/tooling、模型、SOP或资产不能拿旧PASS发布。

```powershell
# 使用本轮新建且不重置的累计预算；先核对已有资产的当前审核来源与远端完整性。
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.evaluation.releases.release_cli validate --live --assets .local/m18/current/assets.json --output .local/m29/content-new.json --budget-state .local/m29/budget.json --auth .local/m17/auth.json --prompt-artifact .local/m29/prompt-v1.json
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.releases.release_cli prepare --assets .local/m18/current/assets.json --validation .local/m29/content-new.json --version m29-prompt-a --prompt-artifact .local/m29/prompt-v1.json --output .local/m29/release-a.json
```

后续登记、发布与回退沿用[完整版本入口](../deephelp-app/README.md#m18-完整版本发布)。`ReleaseManifest.prompts`的保留键`langfuse:intent-fallback`指向工件SHA256，`files`绑定唯一完整路径/hash，工件记录不可变平台版本；原模型、SOP、审核、代码、完整资产和CAS门禁保持。运行从清单读取本地工件，不访问平台Prompt API，不随平台label改变，不因平台不可用切换未经验证文本。当前静态搬运不宣称质量提升。

工作台验证只写本次不可变任务目录，不覆盖保留版本引用的资产验证文件；准备使用所选资产最近验证任务的明确PASS，失败/取消/中断不能退到更早报告。每个准备任务拥有稳定且独立的版本名，显式恢复仍登记同一版本；原prepare_manifest继续核对报告、当前代码/工具、Prompt与资产，不因任务PASS绕过门禁。

catalog可配置`prompt_artifact`用于原`validate/prepare`任务，以及`platform_config`启用候选导出/标注读取。`platform_candidate`为新增受控任务，只提交一个服务端已存在的候选ID和当前revision；0模型调用，独立子进程负责SDK写入，120秒子进程/660秒外层上限，取消等待子进程退出。CLI也可使用`candidate --candidate-id <hash> --revision <整数> --output <新路径>`；所有平台操作都须`--live --config`。

候选导出将整份候选revision摘要、原文本及原观察绑定到确定性trace/observation；读回核对内容、结束状态和范围。只选择原生v3 Score的`ANNOTATION`来源、明确作者、具体observation、`m29_intent_label`分类及支持标签。API评分、数值高分、trace级/其他观察评分不进入可选审核依据。选择平台score后仍须独立标签、理由、目标路由、留出隔离与显式人工确认；原审核事务再核对revision CAS。审核记录保留score ID/摘要及原candidate revision，读取标注不会批准、构建或切换active。修改候选后旧标注不再适用，需重新导出和审核。
