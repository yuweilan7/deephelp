# DeepHelp

用Python复刻客诉意图识别、多轮问题归属、分层记忆、SOP/MCP、调试和评测。当前完成M00设计、M01异步骨架和中间件只读连接验证；业务Agent尚未实现。

| 需要什么 | 入口 |
|---|---|
| 当前事实与待验事项 | [PROJECT_STATE](docs/PROJECT_STATE.md) |
| 能力顺序与检查命令 | [ROADMAP](docs/ROADMAP.md) |
| 应用/隧道启动、凭据与模型额度位置 | [LOCAL_SETUP](docs/LOCAL_SETUP.md) |
| Agent读取范围与Git交付 | [AGENTS](AGENTS.md) |
| M01 API、资源与测试边界 | [应用README](modules/deephelp-app/README.md) |

Python3.14.7；根uv workspace、一个uv.lock、一个Git仓库。业务代码在modules/deephelp-app，运维在infra；后续增量实现，不按M编号重建工程。每次一个特性，从同步main新建feature，验证后中文commit、push、合并并同步分支。

按任务读取：接口查CONTRACTS，架构查ARCHITECTURE，裁决/来源查DECISIONS与SOURCE_MAP，难例查ACCEPTANCE，只读直接前置handoff。学习材料在docs/learning，历史过程资料不作当前实施指令。

原PDF和凭据只留本机。新机器按LOCAL_SETUP提供配置；有免费额度、库能连接都不等于业务链路已验收。
