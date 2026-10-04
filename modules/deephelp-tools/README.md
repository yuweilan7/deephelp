# DeepHelp 独立工具包

本包依赖 `deephelp-app` 的唯一领域类型与会话引擎，提供显式学习、demo、探针、冻结评测、训练及发布准备。运行包没有反向依赖。根开发环境通过唯一uv锁安装两包，独立机器可只安装app wheel；需要演示/学习时再安装tools wheel。

`learning/` 是fake、回放与故障实验；`demo/` 是正式MCP SDK的合成业务stdio服务及合成权益HTTP服务；`probes/` 是受控显式验收；`evaluation/` 是冻结集审计、训练、人工审核与发布准备。M20独立Business服务使用MySQL持久资料；本包demo事实仍来自JSON。

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

如需独立合成审批练习，先用 `python -m deephelp_tools.demo.approval_cli init` 准备专用key/合成SOP，再用 `python -m deephelp_tools.demo.synthetic_rights --port 8024 --key .local/m15/rights.key` 启动受控服务。启动学习扩展前设置本机 `DEEPHELP_LEARNING_RIGHTS_PORT=8024` 和 `DEEPHELP_LEARNING_RIGHTS_KEY=.local/m15/rights.key`。auth中需显式配置synthetic-tenant/synthetic-user-a的演示令牌；个人资料身份不能创建该固定fixture练习。示例key路径来自已有运行入口，不从浏览器接受路径/端口/密钥。

协议页只允许四种固定动作，查询仍使用可信身份与原只读守卫；工作流页读取实际SOP及ApprovalService图。演示先实际MySQL落计划并interrupt，审批绑定operation/版本/参数hash/SOP快照，独立确认后再显式resume；重启后用原operation/session/run读取与恢复。UNKNOWN仍先对账，重复恢复不重复效果；synthetic_rights不改M20资料、不表示真实退款。页面不写checkpoint，也不暴露任意MCP、SQL或文件接口。

扩展还提供零模型实体规则练习、同一原run的意图/归属视图、显式同向量三路检索与只读记忆投影。三路查询使用当前Retrieval服务发布profile及原共享预算，预计一次Embedding、缓存命中可为0；不改变训练资产/门限。原文span在debug脱敏前定位，原始内容从授权历史核对。运行观测使用已保存run的OTel ID、M21分页与私有Grafana深链接；如需实际平台设置，见[观测运行入口](../../infra/observability/README.md#平台与访问)，平台故障明确显示，不重跑业务。
