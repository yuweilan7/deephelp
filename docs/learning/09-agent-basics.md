# 09｜LLM、Agent、tool、SOP与LangGraph

[上一章](08-message-path.md) · [课程入口](README.md)

## 业务问题与目标

模型建议“查订单”，谁决定能不能查、查哪个、何时结束？目标是拆开语言判断和确定性执行，理解受控Agent回合。前置为08章的槽位、事实和计数。

## 一次Agent回合

LLM接收消息/工具描述/schema，返回文本、结构值或tool_calls，本身没有PG访问能力。Agent在模型与工具之间迭代：给上下文→取建议→校验→执行工具→把结果放入下轮上下文→达到终止条件。SOP限定步骤、工具、必填字段、分支和上限；治理版固定快照和节点路径。

tool_call是建议值，ToolGateway负责正式MCP协议、鉴权/schema和结果。模型说成功不构成订单事实；finish_sop也是控制建议，最终结论由事实规则评估。

LangGraph是状态图编排库，当前Conversation是Python顺序编排，审批子流程才使用实际图、interrupt/resume和官方PG saver。13段不是13个图节点，checkpoint不是业务账本。Java状态机可类比节点/边，但暂停状态、恢复参数和副作用仍需单独守卫。

## 实验与预期

离线、合成、远程模型0次；可读01/08章同一次输出：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request
```

answered为model_calls=2、tool_calls=1：RecordedModel建议查订单，获得合成事实后建议finish_sop。stdio协议是真实本机SDK，建议是固定回放，不能证明LLM理解物流或企业泛化。missing/unknown工具0次。

## 按回合读源码

打开[SOPExecutor._loop](../../modules/deephelp-app/src/deephelp_app/application/sop.py)：

1. values从已验证entities建立；expected挑required_slots，模型不能猜未知订单。
2. ModelTool列白名单/schema，增加finish_sop/handoff_sop。
3. await model.chat等待模型端口，共享预算限制回合、重试、工具步数/deadline。
4. 校验建议数量、名称、arguments后才await tools.execute；失败明确停住。
5. observation进入后续消息；finish检查已有事实，不能先凭空结束。

[ReplyComposer](../../modules/deephelp-app/src/deephelp_app/application/reply.py)先生成模板，可选模型只选opening/closing枚举。Java策略接口可类比替换实现；模型输出仍是外部不可信输入。

## 常见误区与反例

- Prompt不能强制权限/幂等/金额规则，代码与账本负责。
- 更强模型不能恢复未召回资料或绕过缺槽。
- 工具名相同不等于结果可信，两端schema/身份及事实证据闭合都要校验。

## 练习与答案

预测模型建议delete_order；或建议query_order但带另一个用户的订单。

答案：前者白名单拒绝，后者参数/归属/结果守卫拒绝。普通消息不能批准旧退款建议，必须走显式绑定命令、唯一领取；后续审批章继续解释。

## 受控专业 Agent委托（M40）

普通 SOP 的模型在同一上下文选择当前工具；协作路径增加一个协调角色和一个拥有独立任务上下文的订单权益角色。协调者先建议 `delegate_task`，专业角色选择 `get_order_benefits`，观察成功后用 `return_evidence` 交还实际证据 ID。它有自己的指令、目标、工具许可和结构化回执；原600确定意图，原SOP继续分支/比较并生成事实回复。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course collaboration
```

预期：`remote_model_calls=0`，`cases`中订单例 `recorded_decisions=3`、`real_stdio_calls=1`；原SOP路径为 `order → decide → applied`，回执 `COMPLETED`，实付 `99.90`、优惠 `10.00`。固定动作只证明机制，实际模型内容另由显式live入口验证。本机stdio SDK与原工具观察真实运行，退出实验后关闭子进程。

按顺序读 [AgentTask/AgentReceipt](../../modules/deephelp-app/src/deephelp_app/domain/models/collaboration.py)、[CollaborationSession.lookup](../../modules/deephelp-app/src/deephelp_app/application/collaboration.py)、[SOPExecutor._flow](../../modules/deephelp-app/src/deephelp_app/application/sop.py)。任务固定父run/question、身份/session、问题输入版本、实体原来源、角色/策略摘要、工具许可和预算引用。正文不能填写这些可信字段。模型返回另一订单、未知角色或假证据均在接收处拒绝；不同来源矛盾仍按原SOP移交，不能让另一个Agent投票创造事实。

`execute`拥有本次 `CollaborationSession`，两个请求没有共享任务列表。`lookup`的await沿当前Task顺序等待模型与原工具，不创建游离后台任务；原ToolGateway拥有协议连接/派发记录和取消释放。所有角色借用同一个 `ExecutionBudget`，模型网关/工具网关分别预留，协调用例不能重复结算。模型超时的预留仍未知，不退款成新额度。

Java可类比一个用例调用受权限约束的策略对象并收到不可变结果，但LLM建议是外部不可信输入，不能等同于已审核的Java代码。Python `await`让出当前事件循环，并非每个角色自动拥有独立线程；这里选择顺序委托，是因为后续业务分支必须先有当前查询证据。

工作台明确选择协作后发送新消息；“事实/时间线”和“工具与工作流”沿所选原run展示协调者、专业角色、槽位来源、实际模型动作和call/证据ID。默认单Agent，读取历史0模型；缺槽先补充，未记录任务不补造协作故事。重投同message_id保留原执行模式，不能借重投把single改成collaboration。

练习：专业角色返回 `COMPLETED` 却引用不存在的证据；或任务来自另一个question但订单号相同；或用同message_id改执行模式。答案：第一种回收校验拒绝，原SOP不能回答成功；第二种父链/归属守卫拒绝，编号相同不提供授权；第三种原消息hash冲突，旧single默认hash保持兼容。若用户缺订单号，预期没有委托、没有工具，原主链先等待补充。


券门槛例为 `recorded_decisions=6`、`real_stdio_calls=2`，两个回执按 `coupon-status → order-benefits` 顺序出现，实付99.90、门槛100.00由原SOP比较。可用/过期券只查券，不机械凑第二个Agent；缺券号仍先等待补槽。Java可以把两个专业端口类比为顺序策略调用，但不能把模型承诺当方法后置条件。

原 [PostgresAgentTasks](../../modules/deephelp-app/src/deephelp_app/adapters/persistence/agent_tasks.py) 借用原账本连接池：begin只在短事务中锁原run，核对原消息hash/归属/版本，COMMIT后才等待模型。phase从delegate→select_tool→tool→return_evidence→closed单调前进；成功工具观察先落OBSERVED再等待回收，完成原回执不可改写。固定深度1/最多2任务/每任务6attempts/80秒同时受原90秒deadline约束；attempts包括格式修复和工具重试。`limit_attempts`只收窄原额度，退出不赠送额度，不允许借其范围并发其他角色。

中断读取沿原session/run的任务表，不补造最终回执：COMPLETED的同绑定任务可直接返回原观察，调用计数与预算增量均0；RUNNING/OBSERVED/UNKNOWN/CANCELLED禁止重新领取。普通新消息不恢复旧run，也不批准旧操作。取消向父传播，仅短保存拥有者可在5秒宽限中写终态；这不保证进程强杀或重复取消后已经落盘，未关闭状态仍需只读核对。任务进度与原最终response分别展示，部分完成不表示整次成功。

练习：券回执完成后订单角色失败；或模型回收前取消，但工具观察已落盘；或两个请求同时领取同子任务。答案：第一例整次失败，保留券完成与订单失败；第二例保存观察和取消、不返回成功证据、不重发；第三例原run行锁及唯一键只允许一个领取者，另一个先读取状态。原消息仍RUNNING时无自动恢复整条会话入口，不能把读回子回执外推为任意checkpoint可续跑。


M40原主链六类固定内容对照：订单、券门槛、缺槽、外归属、来源矛盾、正文越权写建议，single与collaboration各一条。12条原PG回执/工具事实复核通过，六类事实与分支相同；普通订单观察为约4.13秒→9.78秒、券门槛约5.07秒→18.06秒，模型动作更多，未展示回答质量收益。每个成功查询由单路径1个模型选工具变为协作3个公开动作，实际总API还受embedding缓存/路径影响；同组共用网关，第二模式可能命中缓存，所以这些耗时不是冷启动基准。

默认single，只有需要观察独立角色、权限隔离和任务回执时明确选择协作；更多Agent不自动带来更好的事实。发布清单的prompts映射同时绑定agent-policy-v2.json及其策略摘要，原runtime-v2还绑定全部代码/JSON/迁移/前端字节。修正检查器只读原报告/PG并保留原FAIL，不能改业务规则或把旧PASS套新运行字节。


## 版本化业务Skill（M41）

Skill描述一个复用能力的输入/schema、目标、意图许可、工具和输出；SOP决定何时调用、按哪条业务分支收口；MCP提供实际资料。说明文字不变成代码或授权。按顺序读[机器注册表与SkillSession](../../modules/deephelp-app/src/deephelp_app/application/skills.py)、[原SOP执行链](../../modules/deephelp-app/src/deephelp_app/application/sop.py)、[回执值](../../modules/deephelp-app/src/deephelp_app/domain/models/skills.py)。稳定ID/version/hash三者共同确定内容；同版本指令不能覆盖，输入来源沿已确认Entity保留。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course skills
```

预期订单000031实付99.90、优惠10.00，实际stdio调用1次、Skill COMPLETED回执1条，远程模型0次；退出上下文关闭MCP子进程。固定建议仅证明机制，不能证明模型选择质量。工作台选择原run，在“工具与工作流”读Skill/version/hash、输入来源、实际call与证据，浏览0模型；缺槽不启动Skill，取消无终态时保持未知。

Java类比：SkillDefinition类似不可变用例契约，SkillSession类似请求内Interceptor，SOP像业务编排。边界是schema/说明不会自动授予权限，Python await只等待原异步链，客户端由原gateway上下文拥有，不能每个Skill另建连接或新预算。

练习与答案：1. 加refund参数会怎样？输入必须与已确认槽位精确相等，工具前拒绝。2. 改同一version目标？不可变登记拒绝，应创建新候选版本。3. 缺order_id？原SOP返回WAITING_SLOT，模型/工具/Skill均0次。4. 说明写“退款成功”？只有实际只读工具证据进入事实，SOP审批守卫仍决定写建议能否成立。


M41组合实验同一`course skills`增加订单→券例：DEMO-C01实付99.90、券门槛100.00，两个stdio调用，原SOP在threshold收口；父组合1条、子Skill2条回执。coupon输入order_id来自order.order_id实际Fact，再与原槽位精确核对；coupon_id来自原slot。原SOP读回已完成观察，不再次派发；Java可类比顺序CompletableFuture.thenCompose，区别是这些await共享原deadline/尝试计量，取消传播回原拥有者。

组合当前只支持深度2的直接只读叶子、最多4步、16attempts/80秒且受原SOP/请求更小上限约束；递归/环、未登记/漂移子hash、未来/类型不兼容映射、子扩权均拒绝。写Skill未开放，已有SOP提案仍走原M15，并额外绑定Skill摘要。协作模式的叶子可包装已验M40受控角色，保持其原券→条件订单分支，未开放任意委托Skill或通信。

练习与答案：5. 第二步预算耗尽？首步原call/证据保留，整次不输出解决，不能新建子预算继续。6. 查询券时取消？父取消传播，原gateway上下文关闭，未提交终态保持未知；不能断言工具未派发。7. 重跑组合能恢复吗？同执行实例重复ID拒绝；终态消息读取原账本0调用，未终态/UNKNOWN先查原记录，不提供自动重领。8. 旧版本续接？保留F1机器字节与摘要，原问题沿旧版本执行，不把待审批动作绑到新指令。

### 候选、审核与完整发布

“工具与工作流”读取当前Skill、机器schema/许可/依赖和候选差异，模型0次。修改已有叶子的目标时必须使用新版本；组合引用自动形成新版本和精确子hash。理由与确认框完成显式审核，revision CAS阻止重复审核；审核不会改active。管理只对现有学习catalog的owner开放，仍沿同源/CSRF及身份边界。

在“评测与发布”刷新已审核Skill，验证与准备选择同一候选及原资产。验证把快照摘要、审核文件hash和实际运行字节写入内容报告；准备必须匹配，完整清单包含快照及审核原件。激活沿原ReleaseStore的完整资产回读/CAS。生产装配只用清单快照；旧清单无Skill时明确未配置，不偷偷加载新默认。当前run/审批的版本不会热替换。

本地管理与候选试运行复用同一机器核心：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.skill_cli details
py -3.14 -m uv run --locked python -m deephelp_tools.learning.skill_cli trial --registry PATH_TO_NEW_REGISTRY_JSON
```

`details`为0模型只读；`trial`应输出订单/券真实本机stdio事实、远程模型0次。机器JSON只含已允许工具/schema及闭包，不能添加shell/Python代码。`draft --root ROOT --registry JSON`保存候选，`review --root ROOT --candidate DIGEST --revision 1 --identity PRIVATE_IDENTITY_JSON --reason REASON --confirmed`保存审核原件；原完整发布入口另传`--skill-review APPROVED_REVIEW_JSON`。浏览器明确“当前发布版本试运行”创建新业务会话，实际调用模型/MCP并消耗原预算；候选离线试运行不能代替真实模型内容门禁。

Java类比：不可变配置快照、乐观锁revision和一次请求借用的策略对象；Python文件短锁只覆盖同步登记/审核，不能跨await等待模型。选择按意图与原工具许可确定，已发布目标说明进入原模型选工具请求，模型建议仍经机器守卫；没有验证过模型选Skill质量，因此不称智能选择。

练习与答案：9. 已审核后再改JSON？审核/文件/内容hash不匹配，发布拒绝。10. 只改页面列表能否宣称新版本运行？不能，原run必须带同快照/叶子版本及实际call/evidence。11. 回退旧安装继续新Skill消息？旧DTO不识别新字段时只读旧记录，新记录留PG，回到新安装再读；不降格回执、不回滚数据库或清零预算。

## 版本目录与活动资格（M42）

目录决定当前版本允许识别和执行哪些业务。`complaints-v1`保持优惠缺失、券不可用和订单已参加活动三个叶子；`complaints-v2`额外登记`ACTIVITY_ELIGIBILITY_QUERY`，需要`activity_id`。它查询未来参与资格与剩余名额，已成单活动仍走原`ORDER_ACTIVITY_QUERY`。父节点只帮助导航，不能执行；目录、SOP和工具许可同时校验，不能只扩大枚举。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course catalog
```

预期三个固定观察：资格符合、名额7，原SOP进入eligible并RESOLVED；未维护资格进入unknown并HANDED_OFF；缺活动编号进入WAITING_SLOT，模型和工具0次。整个实验远程模型和数据库调用均0，端口观察明确标为固定合成数据。实验复用真实SOPExecutor和唯一目录/ToolResult守卫，源码为[最小入口](../../modules/deephelp-tools/src/deephelp_tools/learning/course/catalog.py)。真实资格由独立Business服务从PG查询，离线固定值不证明某个主体实际有资格。

按[目录](../../modules/deephelp-app/src/deephelp_app/domain/registry.py)→[SOP绑定](../../modules/deephelp-app/src/deephelp_app/application/sop_governance.py)→[MCP双端参数/观察](../../modules/deephelp-app/src/deephelp_app/adapters/mcp/business/mcp_protocol.py)→[PG查询事务](../../modules/deephelp-services/src/deephelp_services/business/repository.py)阅读。新资格资料必须引用同主体的活动，维护沿原CAS；查询在同一只读快照读取活动及资格，持久保存原版本/事实/hash。名额0是已满，null是未维护，不能相互替代。活动已停用、未开始或过期时以活动状态收口；资格符合也不代表已报名或已扣名额。

工作台“业务资料”维护活动与资格，保存后读回版本，竞争保存可能409；“文本与识别”按装配目录做0模型规则练习，选中原run读600实际来源；“工具与工作流”核对同run节点、Fact与PG evidence。浏览页面不补调模型。旧问题继续绑定原目录和SOP快照，旧工具discovery/参数hash也保留；新叶子混入旧目录在模型/工具前拒绝。

Java类比：版本目录像一组不可变用例契约及枚举白名单，SOP像按版本构建的编排表，PG的资格行类似带乐观锁的业务实体。边界是配置字符串不会授予方法调用权；Python await等待原连接/网关，不能为新叶子另建预算，事务不跨模型等待。

练习与答案：1. 只有活动、没有资格行？返回unknown与unknown名额，保留真实规则，不编造原因。2. 有资格且名额0？回答已满，不能保证报名成功。3. 把新工具登记到优惠SOP？TOOL_GRANTS及schema绑定拒绝。4. 缺活动编号能用订单号代替？不能，询问活动编号，原SOP不派发查询。5. 更新资料后重投原message？读取原receipt与不可变事实0调用；新message才查询新的版本。6. 旧安装遇到新问题？旧目录不接收新叶子；保留原记录并由支持该目录的安装读取。

### 下单失败与退款返券

complaints-v3新增两条客诉。下单失败以attempt_id定位一次尝试，不能拿订单号代替，更不能根据“失败”自动重新下单。退款后券没退回以refund_id和coupon_id核对同主体退款、订单、券与回退来源；“我已经退款”只是用户陈述，退款完成须读取PG。先读[业务DTO](../../modules/deephelp-app/src/deephelp_app/domain/models/business.py)的三种记录，再读[仓储](../../modules/deephelp-services/src/deephelp_services/business/repository.py)的put与_complaint_query，最后看[六叶子SOP](../../modules/deephelp-app/src/deephelp_app/resources/sop_data/business-registry-v4.json)。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course complaints
```

六个离线结果：尝试失败原因在explained收口；缺尝试编号WAITING_SLOT且工具0次；退款pending进入waiting；同退款同券且completed/returned进入returned；关联矛盾进入conflict并转人工；缺回退记录进入unknown并转人工。模型与数据库均0次，固定观察通过原SOP/请求/事实/证据守卫，不是实际PG或模型验收。运行时lookup的await等原ToolPort，连接和预算由原请求装配拥有，PG事务不跨模型等待。退款SOP按“冲突→退款完成性→回退状态”顺序，任何分支都不会发券。

工作台按订单→券→退款→回退的关系维护资料，版本竞争返回409，应读回当前版本再判断修改。下单尝试独立维护。回退记录与退款的券号不同而同属当前主体时，保留来源矛盾，原run看到conflict与实际证据；外主体引用仍拒绝。资料更新后旧message重投仍读旧不可变证据，新message才读取新版本。规则练习显示装配目录，浏览原run的事实/路径/错误不消费模型。

Java类比：attempt像请求处理日志，refund与return像两个独立业务聚合，CAS类似@Version。边界是本项目记录为个人合成事实，并没有企业支付对账或事务性发券接口。Python的REPEATABLE READ使本次多记录查询一致，证据随查询事务提交；它不会把来源矛盾变成一致，也不会保证其他系统的副作用。模型选工具不能越过同主体关联和缺槽守卫。

练习与答案：1. 尝试编号不存在？NOT_FOUND，不编造失败原因。2. 退款pending但券声称returned？conflict，不能据券推定退款完成。3. 用户给别人的券号？同主体读取失败，不泄漏券存在性。4. 同主体不同券的回退来源？保留矛盾并人工核对，不把它改成returned。5. completed但没有回退行？unknown，不能承诺发券。6. completed/returned再重投？原receipt重放0模型/工具，不再次发券。7. 缺coupon_id只有refund_id？询问券号，不把退款记录里的券偷当用户确认槽位。

### SOP可视草稿与CAS（M43）

在“工具与工作流”创建已有SOP的独立草稿，编辑lookup查询、branch有限条件和end结论，通过下拉框连接节点。槽位直接对应原工具参数，不提供任意脚本、URL或工具扩权。保存返回服务端真实revision和校验；无效中间图仍可保存修正，错误指出字段/节点。重载读取已保存版本；两个页面竞争时旧revision返回409，应读回再决定修改。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course sop-authoring
```

预期invalid_graph.valid=false并指出order悬空边，修正后saved_revision=3、reloaded_equal=true、stale_revision=VERSION_CONFLICT、published_unchanged=true、model_calls=0。实验使用正式[SOPDraftStore](../../modules/deephelp-app/src/deephelp_app/adapters/releases/sop_drafts.py)与原GovernedSOP，Temp由context manager释放。草稿事实源是文件，官方checkpoint不保存作者内容；新版本只有审核、内容门禁和完整发布后才用于新运行。

同一实验还输出differences、review_status=APPROVED、candidate_matches_review=true和frozen_edit=VERSION_CONFLICT。页面先保存再读取字段差异，核对版本、节点条件和结论，填写理由并明确批准或拒绝。审核writer核对原基准/revision，再保存完整候选与审核原件；审核后不能原地修改，需新建草稿。新SOP版本不能覆盖已有同版本不同内容，拒绝原件也保留理由。

进入“评测与发布”，选择APPROVED的草稿ID、同一资产和已审核Skill，再执行内容验证及准备完整版本。六叶子候选复用真实PG/HTTP目录探针的订单、优惠券和折扣固定相邻样本，借用应用唯一累计writer；不自动重建向量或全量评测。完整ReleaseManifest绑定审核原件、完整候选hash、当前代码/提供方/资产与本次实际内容报告，缺原件、旧报告或篡改都拒绝。准备只登记READY；发布页面显示完整hash与PG revision，明确确认后CAS切换。Java类比是不可变审批配置、制品清单与版本容器；不能把草稿批准类比成对旧订单执行批准。

旧Question、SOPPlan、审批和UNKNOWN继续绑定原snapshot/原receipt；发布不会重新解释旧问题，也不重放副作用。练习与答案：1. 审核后编辑？409，另建草稿。2. 未审核候选直接准备？409。3. 对新hash借用旧PASS？完整门禁拒绝。4. 两页同时发布？旧PG revision冲突，读回实际指针。5. 基准在审核前改变？409，应从新正式版本重建。6. 页面写“批准”能省略实际内容验证吗？不能，审核与内容证据都必须匹配。

Java类比是不可变配置快照、乐观版本锁和服务端Bean Validation；这里验证每条有向无环路径上的事实可用性，不能将其当成任意规则脚本。writer短同步锁只覆盖回读/CAS/原子替换，不跨模型或工具await。练习与答案：1. 删除被连线引用的节点？保存为无效并定位，修正连线后再验。2. 将失败查询接到成功终点？原契约拒绝，缺证据不能成功。3. 加环或eval条件？拒绝。4. 修改外主体草稿？404。5. 旧revision覆盖？409且新稿保持。6. 保存通过等于业务批准？不是；只证明结构，正式运行/审批仍走原守卫。

### SOP受控预演

在同一草稿的“受控预演”确认槽位，再明确开始。默认固定合成事实，真正的查询/分支/终点仍由原SOPExecutor执行；页面显示实际命中第几条条件、原错误、未运行节点及候选hash。任务SUCCEEDED不等于业务成功，应另看RESOLVED、WAITING_SLOT或FAILED。刷新后选择持久任务只读原观察，模型0次；修改草稿后旧hash的结果不能作为新稿验收。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course sop-rehearsal
```

预期三例依次RESOLVED、WAITING_SLOT、FAILED/MODEL_OUTPUT_INVALID；缺槽没有任何Port调用，错误证据不能走成功分支，全部远程模型/业务写入0。阅读[原预演入口](../../modules/deephelp-tools/src/deephelp_tools/learning/replay/sop_rehearsal.py)与[最小实验](../../modules/deephelp-tools/src/deephelp_tools/learning/course/sop_rehearsal.py)。固定适配只返回标明合成的ToolResult，金额/主体/locator/hash仍过正式守卫；不是第二引擎。

任务协调器拥有worker/超时/取消，原装配拥有真实模型/MCP连接和唯一累计预算writer。cancel先保存CANCELLING、取消并join，执行器finally保存部分节点/已派发工具和预算，然后恢复本Task的ContextVar；只有服务端CANCELLED才表示清理完成。关闭页面不取消服务；进程中断只标INTERRUPTED，没有自动再派发。Java类比是任务Future、作用域状态与finally资源释放；Python取消从await抛出，捕获不能省略join或把已有派发说成未调用。

真实只读模式须明确选择并确认，借用已打开原网关、可信身份和累计预算。只验证候选图，关闭Skills组合，不经过600、对话账本或回复润色；含审批提案的图在调用前拒绝。真实结果仍须核对具体PG事实/证据、同hash实际分支与模型调用；离线PASS不能代替完整发布。练习与答案：1. 空槽位？WAITING_SLOT、模型/工具0。2. 错locator？FAILED，不能成功。3. 浏览器超时等于取消？不是，应读原任务或明确取消并等待终态。4. 修改条件再读旧任务？保留旧观察并提示hash不同，不自动重演。5. 审批节点预演？拒绝，不形成SOPPlan。6. 重启后RUNNING？INTERRUPTED，普通读取不恢复业务。

### 同一目录的完整发布

新增叶子不仅是扩大枚举。[发布绑定守卫](../../modules/deephelp-app/src/deephelp_app/adapters/releases/catalog_binding.py)把六叶子目录、语料、冻结dev/test、索引选择和级联策略连接起来；原ReleaseManifest继续绑定代码、工具、提供方、SOP与实际内容报告。旧目录的提示词输出只有三标签，新目录的请求schema允许六个叶子与null，不能让旧Prompt为新目录背书。核验只读资产，不在服务启动时打开测试集或调用模型。

当前六叶子候选没有经过六类校准的FastText，明确采用not_configured，原三类模型保留用于旧版本恢复。实际run的fasttext_model为空，600路径显示规则/召回/结构化兜底，不能将空字段解释成“FastText判断未知”。融合也不是概率；冻结检索组中BM25可能优于融合，不能看test结果后改变dev选出的权重。

先运行前面的catalog与complaints离线入口观察同一SOP，再读[发布装配](../../modules/deephelp-app/src/deephelp_app/bootstrap/runtime/release_runtime.py)的entry：锁只保护每个版本实例的创建，资源由后台任务进入/退出，旧run仍选原manifest。Java类比是不可变版本容器与生命周期管理，不能类比成对同一Spring Bean原地替换配置。

练习与答案：1. SOP六叶子但索引三叶子？拒绝发布/装配，不临时扩大检索scope。2. 只把Prompt文案改成六类？请求schema和内容验收仍需同目录。3. 检索首位是客诉但用户只问物流？600拒绝，无工具派发。4. 服务器Milvus健康但本机报不可用？先查本机转发监听及鉴权；服务器状态不能证明整个客户端路径。5. 回退旧安装要删新表吗？不删，业务事实和预算保留，旧安装读其兼容旧run，再切回新安装读取新字段。
