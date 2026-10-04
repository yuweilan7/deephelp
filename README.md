# DeepHelp

M19已将运行源码按domain/application/adapters/api/bootstrap/resources分层，学习/demo/验收工具可独立安装。开始读[机制学习导航](docs/learning/README.md)，包边界与离线命令见[工具包](modules/deephelp-tools/README.md)；当前业务后端仍是显式demo MCP，M20持久业务另行启动。

用Python复刻客诉意图识别、多轮问题归属、分层记忆、SOP/MCP、调试和评测。三类客诉运行见[业务启动入口](modules/deephelp-app/README.md#业务启动入口)，实际进度与限制见PROJECT_STATE。

从仓库根用 `py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app serve --mcp-module deephelp_tools.demo.mcp_server --corpus demo/data/m09_corpus.jsonl --pointer .local/m09/active.json` 启动现有业务链；先按 LOCAL_SETUP 核对身份、累计预算和真实依赖。`python -m deephelp_app --help` 列出 init/migrate/serve/ask。正式业务通过真实模型、MySQL/Redis/Milvus及本机合成业务MCP；业务事实为合成值，服务绑定127.0.0.1。完整发布按应用文档的 release_cli 入口选取MySQL登记版本。

学习实验与fake在 `deephelp_tools.learning`，显式探针在 `deephelp_tools.probes`，冻结评测/训练选参工具在 `deephelp_tools.evaluation`。运行资源在包内 `assets/runtime`、`assets/demo` 和 `sop_data`；冻结数据与学习数据分别在 `assets/evaluation`、`assets/learning`。业务启动不导入这些工具或读取冻结评测集。

| 需要什么 | 入口 |
|---|---|
| 当前事实与待验事项 | [PROJECT_STATE](docs/PROJECT_STATE.md) |
| 能力顺序与检查命令 | [ROADMAP](docs/ROADMAP.md) |
| 个人学习工作台的下一步 | [M19结构交接](handoffs/M19.md)已完成；M20业务→M21观测→M22前端→M23云运行尚未实施，逐特性启动 |
| 应用/隧道启动、凭据与模型额度位置 | [LOCAL_SETUP](docs/LOCAL_SETUP.md) |
| Agent读取范围与Git交付 | [AGENTS](AGENTS.md) |
| 客诉API、演示与验证边界 | [应用README](modules/deephelp-app/README.md) |

Python3.14.7；根uv workspace、一个uv.lock、一个Git仓库。业务代码在modules/deephelp-app，运维在infra；后续增量实现，不按M编号重建工程。每次一个特性，从同步main新建feature，验证后中文commit、push、合并并同步分支。

按任务读取：接口查CONTRACTS，架构查ARCHITECTURE，裁决/来源查DECISIONS与SOURCE_MAP，难例查ACCEPTANCE，只读直接前置handoff。学习材料在docs/learning，历史过程资料不作当前实施指令。

原PDF和凭据只留本机。新机器按LOCAL_SETUP提供配置；有免费额度、库能连接都不等于业务链路已验收。
