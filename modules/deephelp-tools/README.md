# DeepHelp 独立工具包

本包依赖 `deephelp-app` 的唯一领域类型与会话引擎，提供显式学习、demo、探针、冻结评测、训练及发布准备。运行包没有反向依赖。根开发环境通过唯一uv锁安装两包，独立机器可只安装app wheel；需要演示/学习时再安装tools wheel。

`learning/` 是fake、回放与故障实验；`demo/` 是正式MCP SDK的合成业务stdio服务及合成权益HTTP服务；`probes/` 是受控显式验收；`evaluation/` 是冻结集审计、训练、人工审核与发布准备。真实数据库订单后端将在M20实施，当前demo事实仍来自JSON。

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
