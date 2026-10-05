# DeepHelp

运行源码按domain/application/adapters/api/bootstrap/resources分层，学习/demo/验收工具独立安装。开始读[机制学习导航](docs/learning/README.md)，包边界与离线命令见[工具包](modules/deephelp-tools/README.md)。M20提供独立服务，M25将新源码的关系事实统一到[PostgreSQL业务记录、Business与Retrieval MCP](modules/deephelp-services/README.md)，显式demo入口继续保留；应用可选择两个独立服务复用同一引擎。

用Python复刻客诉意图识别、多轮问题归属、分层记忆、SOP/MCP、调试和评测。三类客诉运行见[业务启动入口](modules/deephelp-app/README.md#业务启动入口)，实际进度与限制见PROJECT_STATE。

先按[PG入口](infra/postgres/README.md)显式设置 `DEEPHELP_POSTGRES_CONFIG` 并执行迁移；PG审批使用官方保存器，旧发布证据不能用于新运行。M30已完成正式PG单写和旧MySQL/Tempo退役；旧源数据/恢复资料继续保留，当前云入口见[部署说明](infra/deployment/README.md)。

从仓库根用 `py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app serve --mcp-module deephelp_tools.demo.mcp_server --corpus demo/data/m09_corpus.jsonl --pointer .local/m09/active.json` 启动现有业务链；先按 LOCAL_SETUP 核对身份、累计预算和真实依赖。`python -m deephelp_app --help` 列出 init/migrate/serve/ask。正式业务通过真实模型、PostgreSQL/Redis/Milvus及本机合成业务MCP；业务事实为合成值，服务绑定127.0.0.1。完整发布按应用文档的 release_cli 入口选取PostgreSQL登记版本。

学习实验与fake在 `deephelp_tools.learning`，显式探针在 `deephelp_tools.probes`，冻结评测/训练选参工具在 `deephelp_tools.evaluation`。运行策略/SOP/Prompt在业务包resources；合成事实、冻结集和学习数据的唯一来源见[datasets职责](datasets/README.md)，工具包安装时包含对应资源。业务启动不导入这些工具或读取冻结评测集。

2026-10-05的[M32–M36路线](docs/ROADMAP.md#可读性与学习体验m32m36)已完成个人免登录、职责整理/清理、[14章Java课程](docs/learning/README.md)、Vue工作台和运行解释。先运行离线课程，再沿[一条消息的调用路径](docs/learning/source-navigation.md)进入具体子系统；当前云安装与通用Chat固定qwen3.8-flash的实际证据见[PROJECT_STATE](docs/PROJECT_STATE.md)。后续[M37–M39收尾规划](docs/ROADMAP.md#最终学习收尾m37m39待实施)分别面向本地核心教学/云端完整体验、Python与Agent的详细中文注释、最终不必要文件清理；仅完成规划，尚未实施，不要求本机复制云端全栈。

| 需要什么 | 入口 |
|---|---|
| 当前事实与待验事项 | [PROJECT_STATE](docs/PROJECT_STATE.md) |
| 能力顺序与检查命令 | [ROADMAP](docs/ROADMAP.md) |
| 个人学习工作台与云运行 | [当前私有云入口](infra/deployment/README.md)、[PG/Langfuse切换交接](handoffs/M30.md)；M23为旧路线历史 |
| 从消息找到源码、测试与子系统 | [源码导航](docs/learning/source-navigation.md)、[M32迁移/清理证据](handoffs/M32.md) |
| Java课程与学习工作台 | [14章课程与离线实验](docs/learning/README.md)、Vue工作台和运行解释已交付；[M37–M39](docs/ROADMAP.md#最终学习收尾m37m39待实施)为待实施收尾计划 |
| 应用/隧道启动、凭据与模型额度位置 | [LOCAL_SETUP](docs/LOCAL_SETUP.md) |
| Agent读取范围与Git交付 | [AGENTS](AGENTS.md) |
| 客诉API、演示与验证边界 | [应用README](modules/deephelp-app/README.md) |

Python3.14.7；根uv workspace、一个uv.lock、一个Git仓库。业务代码在modules/deephelp-app，运维在infra；后续增量实现，不按M编号重建工程。每次一个特性，从同步main新建feature，验证后中文commit、push、合并并同步分支。

按任务读取：接口查CONTRACTS，架构查ARCHITECTURE，裁决/来源查DECISIONS与SOURCE_MAP，难例查ACCEPTANCE，只读直接前置handoff。学习材料在docs/learning，历史过程资料不作当前实施指令。

原PDF和凭据只留本机。新机器按LOCAL_SETUP提供配置；有免费额度、库能连接都不等于业务链路已验收。
