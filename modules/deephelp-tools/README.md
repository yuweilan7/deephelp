# DeepHelp 独立工具包

M25新源码关系事实已统一PostgreSQL；启动前按[PG入口](../../infra/postgres/README.md)设置显式连接。现行旧安装未切换。审批执行/恢复需M26，当前只读历史；旧完整发布不能当作新字节通过的证据。

本包依赖 `deephelp-app` 的唯一领域类型与会话引擎，提供显式学习、demo、探针、冻结评测、训练及发布准备。运行包没有反向依赖。根开发环境通过唯一uv锁安装两包，独立机器可只安装app wheel；需要演示/学习时再安装tools wheel。

`learning/` 是fake、回放与故障实验；`demo/` 是正式MCP SDK的合成业务stdio服务及合成权益HTTP服务；`probes/` 是受控显式验收；`evaluation/` 是冻结集审计、训练、人工审核与发布准备。M20独立Business服务使用PostgreSQL持久资料；本包demo事实仍来自JSON。

从仓库根运行以下入口，帮助、预览和审计均不调用真实模型：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.experiments
py -3.14 -m uv run --locked python -m deephelp_tools.learning.event_cli --help
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.dense_cli preview
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.evaluation_cli audit
py -3.14 -m uv run --locked python -m deephelp_tools.probes.live_probe --help
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.release_cli --help
```

业务运行使用app的正式CLI，显式给出 `--mcp-module deephelp_tools.demo.mcp_server`；Hybrid另给出 `--corpus demo/data/m09_corpus.jsonl` 或实际发布语料。客户端保留工具白名单、可信主体、签名、输入/输出schema、共享预算、超时与取消守卫。`DemoAssembly`/`DemoToolGateway`只在本包明确装配demo，复用运行实现。

完整发布的 `prepare/validate` 位于 `deephelp_tools.evaluation.release_cli`，`register/activate/rollback/status/references/retire/serve` 位于 `deephelp_app.bootstrap.release_cli`。准备同时绑定运行字节和工具/输入指纹；serve无需安装评测工具，也不重新读取冻结集。

仓库中冻结数据唯一来源为[ datasets ](../../datasets/README.md)，demo事实唯一来源为[demo/data](../../demo/README.md)。构建tools wheel时将同一份字节纳入包资源，安装后不依赖仓库路径。学习数据放在本包resources/learning。工作区中的路径与安装wheel中的资源选择顺序详见assets.py。

学习路线见[机制导航](../../docs/learning/README.md)，各模块既有运行与验收参数继续见[应用README](../deephelp-app/README.md)。pytest的收集前网络守卫禁止远程DNS/TCP，允许loopback/受控stdio；它不声称覆盖任意子进程、代理或未来传输。demo子进程只加载合成服务和业务类型，模型执行仍由明确live入口及共享预算控制。

## 浏览器学习扩展

安装tools后，以 `python -m deephelp_tools.learning.workbench serve` 代替基本app入口，其他业务/检索/身份/预算参数完全复用[主工作台启动](../deephelp-app/README.md#m22浏览器工作台)。基础app只显示已装配页面；浏览/固定协议实验不请求模型。

### 评测与飞轮目录

将[目录模板](learning-catalog.example.json)复制到自己的`.local/learning/catalog.json`，按实际已有报告/资产修改映射；移除不存在的条目。路径由服务器配置，浏览器只提交映射ID、候选ID或任务ID。最多16份报告/16个资产；单份浏览报告上限8MB，完整大报告使用已有汇总索引。模板没有凭据正文；`auth`指向原M18验证所需的专用合成身份配置，`redaction_key`沿用现有稳定32字节key，不能重置。`budget_state`与应用`--budget-state`使用同一累计文件；真实构建/验证复用已经打开的共享预算writer。

```powershell
$env:DEEPHELP_LEARNING_CATALOG = '.local/learning/catalog.json'
# 随后使用上面的 tools.learning.workbench serve 与原业务/检索/身份/预算参数
```

只有`owner`这一明确配置的个人学习主体可浏览该本机管理目录；其他已登录身份仍只使用自己的业务工作台，目录读写404。全局审核/完整版本来自当前PostgreSQL，不另建事实源。页内主链采集仅接受已完成的专用`m18-collect`合成run，冻结评测输入不能回收为训练；内容生成沿用原[飞轮入口](../deephelp-app/README.md#m18-审核后的反馈闭环)。审核复用原revision守卫、留出隔离和三路队列，浏览器只能明确人工审核，不能伪造确定性证据注册者。

进入页面先读取计划与共享剩余预约数。冻结审计和四路离线回归为0远程模型；离线计数中的模型适配动作不等于远程API调用。构建基线12条语料、最多10个批准来源，默认每8条一批，预计2–3次Embedding（重试受原门禁）；FastText只在相应路由获批时训练。验证使用原M17/M18固定11会话/14消息、真实服务/模型，预计1–113次远程调用，实际依规则/检索/SOP路径；token范围是计划估计，API usage和预算预留另计，推理沿供应商配置，不关闭或裁剪有效上下文。预算不足会停止并保留诊断；已知不足以完成构建批次时在启动前拒绝。

显式任务仅`audit/offline/build/validate/prepare`，不接收命令、模型配置或任意路径；同一进程一个重任务、1800秒总上限、最多3次尝试、最多100份持久任务记录。取消等待原适配器退出再释放槽位；硬中断重启标`INTERRUPTED`，仅显式恢复才另建不可变尝试目录。已经完成或未知提交的资产/验证/版本登记先对账，原M18队列lease/尝试仍有效，不盲重放。任务`SUCCEEDED`只表示入口返回；`evidence_status`才是实际PASS/REJECTED/PREPARED/READY。任务目录保留状态和仍需恢复/浏览的产物，不作临时目录清空。

候选不修改active。`prepare`复用`prepare_manifest`及`ReleaseStore.register`，完整发布和回退复用`ReleaseStore.switch`、目标hash与revision CAS；回退还绑定当前previous。代码/工具/供应商/SOP/审核或绑定资产失效时旧报告PASS也不可发布。页面不自动执行训练、全量live或切换版本；本轮UI验收没有新质量发布。

M25新PG路线保留已迁移的操作/审计/历史checkpoint读取，暂不启用创建、批准或恢复图。学习页显示M26门禁，按原operation/session/run仅读取本主体的合成历史；旧执行步骤属于bak/mysql-otel，M26官方saver实测通过后再接入。普通消息仍不能批准旧操作，UNKNOWN仍须对账；synthetic_rights不改M20资料、不表示真实退款。页面不写checkpoint，也不暴露任意MCP、SQL或文件接口。

扩展还提供零模型实体规则练习、同一原run的意图/归属视图、显式同向量三路检索与只读记忆投影。三路查询使用当前Retrieval服务发布profile及原共享预算，预计一次Embedding、缓存命中可为0；不改变训练资产/门限。原文span在debug脱敏前定位，原始内容从授权历史核对。运行观测使用已保存run的OTel ID、M21分页与私有Grafana深链接；如需实际平台设置，见[观测运行入口](../../infra/observability/README.md#平台与访问)，平台故障明确显示，不重跑业务。
