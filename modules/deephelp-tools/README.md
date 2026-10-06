# DeepHelp 独立工具包

M45独立数据机制：`py -3.14 -m uv run --locked python -m deephelp_tools.learning.course independent-quality`，默认离线/0模型，教学夹具不声称质量。实际私有来源用 `python -m deephelp_tools.evaluation.quality.independent_cli --intake <脱敏输入JSON> --annotation <盲标A收据> --annotation <盲标B收据> [--adjudication <独立裁决>] --postgres <原PG配置> --tenant <已验证主体> --user <主体> --output <新冻结JSON> --report <必要摘要>`；有实际许可/来源声明，输入含固定train/dev/holdout，收据绑定input_hash且不可看被测输出。`--protocol-report <文件>`另验隔离PG竞争/回滚/精确测试行清理；全部0模型。来源、未决/锁定状态沿原评测页，契约见[第14章](../../docs/learning/14-evaluation-release.md#独立来源双盲标与锁定留出集)。

本地核心先运行`course request`，不需PG、云凭据、旧.local或前端built；环境准备和最短路径见[课程](../../docs/learning/README.md#本地核心最短路径)。真实业务/完整工作台才按[PG入口](../../infra/postgres/README.md)配置并迁移，M26审批使用官方保存器。正式云安装与源码分别见[PROJECT_STATE](../../docs/PROJECT_STATE.md)，旧完整发布绑定其原安装字节。

本包依赖 `deephelp-app` 的唯一领域类型与会话引擎，提供显式学习、demo、探针、冻结评测、训练及发布准备。运行包没有反向依赖。根开发环境通过唯一uv锁安装三个workspace包，独立机器可只安装app wheel；需要演示/学习时再安装tools及其依赖。

`learning/` 是fake、回放与故障实验；`demo/` 是正式MCP SDK的合成业务stdio服务及合成权益HTTP服务；`probes/` 是受控显式验收；`evaluation/` 是冻结集审计、训练、人工审核与发布准备。M20独立Business服务使用PostgreSQL持久资料；本包demo事实仍来自JSON。

evaluation按retrieval/quality/feedback/releases/migration/platform归组；probes按models/retrieval/protocol/conversation/approval/releases/platform/migration归组。learning/workbench组合工作台路由，replay/faults/fakes分别提供回放、故障与替代端口。原命令模块仍转调同一个main实现，内部import和patch使用职责路径。单元测试在tests/unit的对应目录，跨组件场景在tests/integration；调用者、资源拥有者与失败处理见[源码导航](../../docs/learning/source-navigation.md)。

从仓库根运行以下入口，帮助、预览和审计均不调用真实模型：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course sop-authoring
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course sop-rehearsal
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

课程完整topic为types、syntax、request、awaitables、coordination、cancellation、resources、retrieval、memory、observations、evaluation、collaboration、skills。命令帮助和普通实验都没有live入口；审批章直接复用原测试中的ApprovalService/真实LangGraph，安装后的课程不依赖测试包。任务结束不产生新云发布、正式PG事实或模型质量结论。

## 浏览器学习扩展

完整源码学习使用`py -3.14 -m uv run --locked python -m deephelp_tools.learning.workbench serve`替代基本app入口，其他业务/检索/身份/预算/完整发布参数完全复用[主工作台启动](../deephelp-app/README.md#m22浏览器工作台)。先用该命令的`--help`核对参数，再提供自己的有效配置；它不是零配置离线演示。基础app提供业务/历史/解释，tools明确装配协议/工作流/分析与可选评测路由。已有完整云安装按[当前云入口](../../docs/LOCAL_SETUP.md#当前云端个人入口)访问，本机无需装配这些服务；云/本地课程复用同一核心，依赖失败不回退回放。

`DEEPHELP_LEARNING_CATALOG`选择受控评测目录；未配置时没有评测扩展路由。合成权益学习另需成对的`DEEPHELP_LEARNING_RIGHTS_PORT/DEEPHELP_LEARNING_RIGHTS_KEY`，key为原32-byte文件，与基本业务`--rights-*`装配分别配置；个人主体不接管synthetic练习。catalog中的平台历史映射是独立可选项，未配置时显示实际空态，不影响按原run读取Langfuse。浏览/帮助/历史0模型，明确业务提问、检索比较及任务构建/验证才进入相应共享预算。

### 评测与飞轮目录

将[目录模板](learning-catalog.example.json)复制到自己的`.local/learning/catalog.json`，按实际已有报告/资产修改映射；移除不存在的条目。路径由服务器配置，浏览器只提交映射ID、候选ID或任务ID。最多16份报告/16个资产；单份浏览报告上限8MB，完整大报告使用已有汇总索引。模板没有凭据正文；`auth`指向原M18验证所需的专用合成身份配置，`redaction_key`沿用现有稳定32字节key，不能重置。`budget_state`与应用`--budget-state`使用同一累计文件；真实构建/验证复用已经打开的共享预算writer。

```powershell
$env:DEEPHELP_LEARNING_CATALOG = '.local/learning/catalog.json'
# 随后使用上面的 workbench serve 与原业务/检索/身份/预算/发布参数
```

只有`owner`这一明确配置的个人学习主体可浏览该本机管理目录；其他可信身份仍只使用自己的业务工作台，目录读写404。全局审核/完整版本来自当前PostgreSQL，不另建事实源。页内主链采集仅接受已完成的专用`m18-collect`合成run，冻结评测输入不能回收为训练；内容生成沿用原[飞轮入口](../deephelp-app/README.md#m18-审核后的反馈闭环)。审核复用原revision守卫、留出隔离和三路队列，浏览器只能明确人工审核，不能伪造确定性证据注册者。

进入页面先读取计划与共享剩余预约数。冻结审计和四路离线回归为0远程模型；离线计数中的模型适配动作不等于远程API调用。构建基线12条语料、最多10个批准来源，默认每8条一批，预计2–3次Embedding（重试受原门禁）；FastText只在相应路由获批时训练。验证使用原M17/M18固定11会话/14消息、真实服务/模型，预计1–113次远程调用，实际依规则/检索/SOP路径；token范围是计划估计，API usage和预算预留另计，推理沿供应商配置，不关闭或裁剪有效上下文。预算不足会停止并保留诊断；已知不足以完成构建批次时在启动前拒绝。

显式任务为`audit/offline/build/validate/prepare/platform_upload/platform_candidate`，不接收命令、模型配置或任意路径；同一进程一个重任务、1800秒总上限、最多3次尝试、最多100份持久任务记录。取消等待原适配器退出再释放槽位；硬中断重启标`INTERRUPTED`，仅显式恢复才另建不可变尝试目录。已经完成或未知提交的资产/验证/版本登记先对账，原M18队列lease/尝试仍有效，不盲重放。任务`SUCCEEDED`只表示入口返回；`evidence_status`才是实际PASS/REJECTED/PREPARED/READY。任务目录保留状态和仍需恢复/浏览的产物，不作临时目录清空。

候选不修改active。`prepare`复用`prepare_manifest`及`ReleaseStore.register`，完整发布和回退复用`ReleaseStore.switch`、目标hash与revision CAS；回退还绑定当前previous。代码/工具/供应商/SOP/审核或绑定资产失效时旧报告PASS也不可发布。页面不自动执行训练、全量live或切换版本；浏览与审核本身不证明模型质量。

学习页按原operation/session/run读取本主体的官方checkpoint和实际审批图；显式配置合成权益服务并使用专用演示身份后，可创建固定练习、批准/拒绝/撤销及恢复同一原操作。普通消息仍不能批准旧操作，UNKNOWN先对账；synthetic_rights不改M20资料、不表示真实退款。页面不允许直接写checkpoint，也不暴露任意MCP、SQL或文件接口。旧reader仅供迁移资格验证，正式app不包含旧自建codec；转换与历史逐状态恢复见[M26交接](../../handoffs/M26.md)。

扩展还提供零模型实体规则练习、同一原run的意图/归属视图、显式同向量三路检索与只读记忆投影。三路查询使用当前Retrieval服务发布profile及原共享预算，预计一次Embedding、缓存命中可为0；不改变训练资产/门限。原文span在debug脱敏前定位，原始内容从授权历史核对。运行观测先校验已保存run的主体与session，再读取已登记的Langfuse观测；显示来源、实际generation用量、版本及暂停/恢复前序，管理员平台独立登录。未登记原生观测的历史明确标为legacy-tempo，保留[M21历史入口](../../handoffs/M21.md)。平台故障明确显示，不重跑业务。

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


## M40 专业Agent实验与内容验收

离线最小实验复用原SOP/委托用例及stdio工具，远程模型0次：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course collaboration
```

显式真实模型+本机正式stdio MCP固定订单内容入口如下；累计预算先按当前授权初始化，不自动重置已有文件。它证明订单角色正常与注入反例，不是PG/正式主链或企业退款验收。

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.probes.conversation.collaboration_probe --live --providers modules/deephelp-app/providers.example.json --budget-state .local/m40/budget-feature1.json --output .local/m40/order-content.json
```

使用新output路径保留不可变报告；身份/数据为专用合成输入。核心解释、预期结果和练习答案见[09章](../../docs/learning/09-agent-basics.md#受控专业-agent委托m40)。正式主链显式execution_mode=collaboration，默认single；原页面选中run查看实际委托/证据，浏览不调用模型。

M40子任务PG协议（固定动作，远程模型0次）：

```powershell
uv run --locked python -m deephelp_tools.probes.conversation.task_journal_probe --live --config PRIVATE_PG_CONFIG --output NEW_PRIVATE_REPORT
```

该显式入口在原PG创建唯一合成消息，证明两源完成回收/重读0调用、竞争领取/CAS/归属和连接重开后RUNNING禁止重领；不是任意会话恢复。券实际模型内容选择collaboration_probe的`--case coupon-threshold --case coupon-conflict`，共享预算和新报告必填；不自动扩大历史样本。

M40原主链受影响内容对照（六类固定样本，每类single/协作，完整历史评测另行显式启动）：

```powershell
uv run --locked --env-file .env.local python -m deephelp_tools.probes.conversation.collaboration_delivery --live --assets PRIVATE_PREPARED_ASSETS --providers EXACT_PROVIDERS --auth PRIVATE_AUTH --budget-state EXISTING_CUMULATIVE_BUDGET --output NEW_PRIVATE_REPORT
```

配置原PG、Redis/Milvus连接与所需endpoint，先用真实协议确认依赖；只有端口可连不足。比对实际事实/分支/原13段/600、权限与证据，重投/历史0调用；共用网关可能命中embedding缓存，耗时按观察解释，不宣称质量或冷启动收益。角色指令/策略/许可JSON沿原精确发布绑定。

检查器与原响应Outcome契约的等价修正可用同入口`--review-report ORIGINAL_REPORT --output NEW_REVIEW_REPORT`只读复核原PG关键回执字段；原采集报告及hash保留，新增模型/工具0。runtime/资产/providers/策略/样本必须相同且齐全，不允许用缺样本或改业务预期换PASS；新检查器tooling摘要及原采集器摘要分别绑定。


M41单Skill实验：`py -3.14 -m uv run --locked python -m deephelp_tools.learning.course skills`。真实固定内容复用原collaboration_probe，加`--execution-mode single --case normal-order --case injection-order`，仍须--live/准确providers/原累计budget-state/新output；证据为真实模型与stdio，非PG正式部署。[09章](../../docs/learning/09-agent-basics.md#版本化业务skillm41)说明预期与Java边界。

M41管理：`python -m deephelp_tools.learning.skill_cli details`读取机器契约0模型，`trial --registry NEW_REGISTRY_JSON`用同核心/固定建议/真实本机stdio；draft/review参数见`--help`和09章。原工作台“工具与工作流”提供详情/差异/候选/显式审核/当前发布版本试运行；“评测与发布”的validate/prepare选择同一已审核Skill。正式六类受影响内容复用上方collaboration_delivery，加`--single-only --skill-review PRIVATE_APPROVED_REVIEW_JSON`；原release_cli的validate/prepare也接受`--skill-review`，完整门禁绑定快照/审核原件/本次代码与内容，不改旧manifest。全部真实命令须先核验真实依赖并声明固定样本/原共享预算，不因课程或页面浏览执行live。
## M42 目录学习与针对性验收

最小离线入口`py -3.14 -m uv run --locked python -m deephelp_tools.learning.course catalog`复用真实SOP/目录守卫，输出资格符合7、未知转人工、缺编号0调用；解释见[09章](../../docs/learning/09-agent-basics.md#版本目录与活动资格m42)。远程模型/数据库均0，固定端口不代替真实内容。

hybrid_cli支持`--registry-version complaints-v2 --namespace NEW_NAMESPACE --corpus FROZEN_CORPUS --dev-queries DEV_JSON --test-queries TEST_JSON`，import/tune/evaluate/activate使用同累计预算、独立清单与新报告路径。`tune --tune-weight 0.5`可在看test之前明确只验证既有候选权重；默认仍搜索0.25/0.5/0.75。dev/test与reference保持来源/变体隔离，新标签不能导入旧scope。

真实PG/正式HTTP MCP/600/SOP/事实/receipt验收入口为`python -m deephelp_tools.probes.conversation.catalog_probe --live --phase eligibility`；必填auth、service-credentials、providers、pointer、corpus、sop-directory、cascade-policy、budget-state、output见`--help`。配置原PG和依赖后再执行，固定Chat与Embedding使用精确providers；新语料使用独立scope/校准绑定，未校准分数接管时gates明确为空，不能套旧目录阈值。权限/CAS证据不经模型，成功消息重投和历史0调用。失败报告保留，`--cases CASE...`只重跑失败及受影响相邻样本；不更改样本预期换PASS。此入口不切正式发布。

六叶子离线实验为`uv run --locked python -m deephelp_tools.learning.course complaints`。真实两类投诉使用同catalog_probe `--phase complaints`，选择complaints-v3的21条corpus及对应8dev/8test、独立index/pointer/selection/SOP目录/累计预算。hybrid_cli支持`--registry-version complaints-v3`，开发权重选择先于heldout test；默认不让原三叶子FastText或旧分数阈值决定新标签。13条固定内容含两新叶子、缺槽、不存在、等待、已回退、矛盾、未知/外主体及原四相邻路径，维护CAS不经模型。

M44离线机制入口：`py -3.14 -m uv run --locked python -m deephelp_tools.learning.course corpus-authoring`，复用原解析/导入/审核纯核心，模型0。在线原“识别与检索”提供PG作者工作区、叶子选择、revision、明确列映射预览/逐行错误/整批确认、人工审核及导出；依赖原ledger PG及learning catalog owner。[10章](../../docs/learning/10-retrieval.md#语料作者导入与审核m44)解释字段、Java类比与响应未知对账，不把草稿导出当正式发布。

审核后在同页明确冻结候选，构建/比较沿`/learning/evaluation/tasks`的唯一重任务，取消与恢复复用原累计writer。learning catalog的`corpus_splits`须同时配置dev/test两条受控路径；构建先冻结再调用模型，候选结果放task_root/corpus/version_hash。配置只读入口不自动构建；候选pointer不是正式release channel，完整发布另验。默认Rerank关闭保持未运行，原三路各自保留分数/文本/来源和实际耗时。

M44发布仍在同页：原反馈采集/人工批准corpus→导入精确revision为DRAFT→作者人审/新冻结→corpus_build→corpus_gate→原validate/prepare/publish。learning catalog额外配置`corpus_baseline`（已有assets键）及`corpus_sop_review`/`corpus_skill_review`（当前已批准原件）；不是客户端路径。corpus_gate借当前Chat/policy消费已执行检索观察，不再Embedding，正式路线由原装配固定，其非退化及全部分类正确才返回task资产ID供原validate/prepare，并记录原反馈lineage。其他检索路线的诊断下降仍完整展示。代码/配置变化拒绝续用旧分类门禁；只读版本diff、任务和报告0模型。

实际独立反馈来源的有限入口为原catalog_probe `--phase corpus-feedback`，auth第一条必须为原`synthetic-tenant/synthetic-user-a`，原Conversation消息channel固定m18-collect；新合成订单事实及新固定措辞通过原PG/HTTP MCP，采集报告不能替代release阶段八场景。完整发布仍选择`--phase release`及六叶子/两未知范围，corpus_binding进入原assets/files与ReleaseManifest，旧资产/hash不改。详细学习/错误/练习见[10章](../../docs/learning/10-retrieval.md#原反馈新版本与完整发布)。

M45配对课程：`py -3.14 -m uv run --locked python -m deephelp_tools.learning.course paired-quality`，0模型、PARTIAL/完整分母反例。真实`paired_cli freeze`必填`--contract --dataset --providers --judge --pointer --binding`；默认dev，源/配置/输入与单因素不可覆写。`paired_cli run --contract <冻结合同> --directory <原实验目录> --budget <同一累计文件>`；UNKNOWN恢复另明确`--retry-uncertain`，最多三次，不重复已完成观察。服务器catalog的`quality_experiments`登记固定合同路径；原评测页读报告0模型，明确`independent_pair`才进入原LearningJobs。详见[第14章](../../docs/learning/14-evaluation-release.md#同版本条件与配对分母)。

M45审核课程：`py -3.14 -m uv run --locked python -m deephelp_tools.learning.course quality-improvement`，0模型/PG，输出零收益及关键退化拒绝。真实审核/修订/关闭复用原PG，命令参数中的路径均为已核定私有原件；先固定标准，再候选，再唯一holdout，不能从holdout选问题。`review/revise/close`自身0模型；只有显式paired run调用远端。

```powershell
# 以下占位参数用于新独立实验；本次M45留出集已消费，不能重复调参。
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.quality.improvement_cli review --postgres PRIVATE_PG --tenant OWNER_TENANT --user OWNER_USER --dev-report COMPLETE_DEV_REPORT --proposal PREDECLARED_PROPOSAL --output NEW_REVIEW
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.quality.improvement_cli revise --postgres PRIVATE_PG --tenant OWNER_TENANT --user OWNER_USER --review NEW_REVIEW --prompt-text NEW_PROMPT_TEXT --output NEW_PROMPT_ARTIFACT
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.quality.paired_cli freeze --split holdout --review NEW_REVIEW --review-hash EXACT_REVIEW_HASH --postgres PRIVATE_PG --tenant OWNER_TENANT --user OWNER_USER --prompt NEW_PROMPT_TEXT --contract NEW_HOLDOUT_CONTRACT --dataset NEW_FROZEN_DATASET --providers EXACT_PROVIDERS --judge EXACT_JUDGE --pointer EXACT_POINTER --binding EXACT_BINDING
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.quality.paired_cli run --contract NEW_HOLDOUT_CONTRACT --directory NEW_EXPERIMENT_DIRECTORY --budget EXISTING_CUMULATIVE_BUDGET
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.quality.improvement_cli close --postgres PRIVATE_PG --tenant OWNER_TENANT --user OWNER_USER --review NEW_REVIEW --prompt NEW_PROMPT_ARTIFACT --holdout-report COMPLETE_HOLDOUT_REPORT --contract NEW_HOLDOUT_CONTRACT --output NEW_CLOSURE
```

Proposal字段/事前标准见`improvement.Proposal/Criteria`及[14章](../../docs/learning/14-evaluation-release.md#事前审核与一次留出集复验)。闭环REJECTED保留baseline；ADOPTION_ELIGIBLE才可给原`release_cli prepare`增加`--quality-review NEW_CLOSURE --prompt-artifact NEW_PROMPT_ARTIFACT`，仍须原assets/validation/完整门禁，发布/安装另沿原链。catalog可登记`quality_review`供原prepare同一附加门禁，GET quality-improvements读取真实PG证据，不执行模型。完整实验同合同重读0调用；输入/实现漂移默认拒绝。日志容量修复只接受有完整前后源码证据的服务端repair原件，原清单边界和冻结合同不改写。
