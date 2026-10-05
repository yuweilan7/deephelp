# DeepHelp

运行源码按domain/application/adapters/api/bootstrap/resources分层，学习/demo/验收工具独立安装。开始读[机制学习导航](docs/learning/README.md)，包边界与离线命令见[工具包](modules/deephelp-tools/README.md)。M20提供独立服务，M25将新源码的关系事实统一到[PostgreSQL业务记录、Business与Retrieval MCP](modules/deephelp-services/README.md)，显式demo入口继续保留；应用可选择两个独立服务复用同一引擎。

用Python复刻客诉意图识别、多轮问题归属、分层记忆、SOP/MCP、调试和评测。三类客诉运行见[业务启动入口](modules/deephelp-app/README.md#业务启动入口)，实际进度与限制见PROJECT_STATE。

本地先运行核心原理：在新克隆的仓库根准备Python3.14.7/uv0.12.13和锁定依赖，再执行原课程。

```powershell
py -3.14 -m uv sync --locked --all-packages
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request
```

无需旧`.local`、云凭据、SSH隧道、Node或本机PG/Redis/Milvus/Langfuse。先观察缺槽→补充→原事实→零调用重投，再沿[本地最短路径](docs/learning/README.md#本地核心最短路径)学归属、检索、异步和审批。实验用固定模型响应、内存适配器和合成资料复用原核心；远程模型0次，不能当作模型质量或真实持久性证据。依赖准备可能下载包，安装完成后可将`uv run --locked`改为`uv run --offline --locked`。

云端使用已有完整个人学习工作台，本机只需浏览器和已经配置的personal SSH隧道：

```powershell
./infra/client/tunnel.ps1 -Profile personal -Action start
./infra/client/tunnel.ps1 -Profile personal -Action health
```

然后打开[个人学习工作台](http://127.0.0.1:18080/)。新机器的SSH配置按[LOCAL_SETUP](docs/LOCAL_SETUP.md#本机配置与新机器接手)提供。云端保留真实模型、PostgreSQL事实/历史、两个MCP、原run解释与观测/评测入口；页面浏览0模型，明确执行才调用。当前能力及未配置项见[云教学边界](docs/LOCAL_SETUP.md#当前云端个人入口)，安装与恢复见[部署说明](infra/deployment/README.md)。两种环境按用途验收，不要求本机复制云端全栈。

需要自行装配真实业务时，基础`deephelp_app serve`与完整`deephelp_tools.learning.workbench serve`的选择见[当前运行方式](docs/LOCAL_SETUP.md#当前学习与运行入口)。完整运行显式配置PG/模型/身份/预算/发布资产；基础serve不自动装配全部学习路由。M30旧MySQL/Tempo源数据与恢复资料保留，不能作为当前默认启动配置。

学习实验与fake在 `deephelp_tools.learning`，显式探针在 `deephelp_tools.probes`，冻结评测/训练选参工具在 `deephelp_tools.evaluation`。运行策略/SOP/Prompt在业务包resources；合成事实、冻结集和学习数据的唯一来源见[datasets职责](datasets/README.md)，工具包安装时包含对应资源。业务启动不导入这些工具或读取冻结评测集。

2026-10-05的[M32–M36路线](docs/ROADMAP.md#可读性与学习体验m32m36)交付个人免登录、职责整理、14章Java课程、Vue工作台和运行解释。[M37–M39收尾路线](docs/ROADMAP.md#最终学习收尾m37m39)中M37已打通本地核心/云端教学入口；M38中文讲解与M39清理尚未启动。当前云安装与固定通用Chat选择见[PROJECT_STATE](docs/PROJECT_STATE.md)，从[一条消息的调用路径](docs/learning/source-navigation.md)阅读同一套实现。

| 需要什么 | 入口 |
|---|---|
| 当前事实与待验事项 | [PROJECT_STATE](docs/PROJECT_STATE.md) |
| 能力顺序与检查命令 | [ROADMAP](docs/ROADMAP.md) |
| 个人学习工作台与云运行 | [当前私有云入口](infra/deployment/README.md)、[PG/Langfuse切换交接](handoffs/M30.md)；M23为旧路线历史 |
| 从消息找到源码、测试与子系统 | [源码导航](docs/learning/source-navigation.md)、[M32迁移/清理证据](handoffs/M32.md) |
| 本地核心与云端完整教学 | [最短离线路径](docs/learning/README.md#本地核心最短路径)、[云工作台](docs/LOCAL_SETUP.md#当前云端个人入口)、[M37交接](handoffs/M37.md) |
| 应用/隧道启动、凭据与模型额度位置 | [LOCAL_SETUP](docs/LOCAL_SETUP.md) |
| Agent读取范围与Git交付 | [AGENTS](AGENTS.md) |
| 客诉API、演示与验证边界 | [应用README](modules/deephelp-app/README.md) |

Python3.14.7；根uv workspace、一个uv.lock、一个Git仓库。业务代码在modules/deephelp-app，运维在infra；后续增量实现，不按M编号重建工程。每次一个特性，从同步main新建feature，验证后中文commit、push、合并并同步分支。

按任务读取：接口查CONTRACTS，架构查ARCHITECTURE，裁决/来源查DECISIONS与SOURCE_MAP，难例查ACCEPTANCE，只读直接前置handoff。学习材料在docs/learning，历史过程资料不作当前实施指令。

原PDF和凭据只留本机。新机器按LOCAL_SETUP提供配置；有免费额度、库能连接都不等于业务链路已验收。
