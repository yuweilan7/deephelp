# DeepHelp

用 Python 复刻《客诉场景的自动驾驶 DeepHelp》的核心机制：分层意图识别、多轮问题归属、分层记忆、SOP/MCP 工具执行、调试与评测。当前只有 M01 异步骨架，业务功能和真实联调尚未完成。

## 先看这两份

- [路线图](docs/ROADMAP.md)：每个模块做成什么、哪些是核心、哪些可后移、怎样分支和验收。
- [当前状态](docs/PROJECT_STATE.md)：已经实现和验证了什么，接下来允许做什么。

M00–M21 是 22 份能力规格，包含已完成设计、核心复刻、工程加固和可选扩展。不是 22 个服务，也不要求每次做完一整个 M。第一次真实闭环在 M08；核心深化完成后，审批恢复、部署演练、物流迁移和框架比较可单独选择。

## 每次的工作方式

从 M02 起：同步 main → 从 main 新建一条 feature 分支 → 只做一个特性 → 测试和必要真实验证 → add、中文 commit、push feature → 合并 main 并复验 → push main → 同步 feature 并核对本地/远端 SHA → 回到 main。依赖千问 API 或腾讯云中间件的特性，真实依赖失败就停下来报告，不靠 Mock 通过验收。

只维护一份短计划和一份模块验收记录；无需额外会话、递归 Prompt、多个 Agent 或一组交接文件。你可以直接说“实施 Mxx 的某个特性”，Agent 按 [AGENTS.md](AGENTS.md) 和 [规划规则](docs/PLAN_MODULE_PROMPT.md) 执行。

## 工程和运行入口

Python 3.14.7；根 uv workspace 和一个 `uv.lock`；业务代码在 `modules/deephelp-app/`。MySQL 保存业务事实，Redis/Milvus 是可重建缓存或索引。保留现有 `infra/`，不重新安装 P00。

本机 PATH 中的 uv 版本较旧，以下使用已验证的 Python 3.11 uv 启动器；实际应用解释器仍为 Python 3.14.7。安装、启动、检查和异步实验见 [应用 README](modules/deephelp-app/README.md)。Windows PowerShell 从仓库根运行：

```powershell
py -3.11 -m uv sync --locked --all-packages
py -3.11 -m uv run --locked uvicorn deephelp_app.app:create_app --factory --host 127.0.0.1 --port 8000
py -3.11 -m uv run --locked pytest
```

`/health` 只验证骨架；`/converse` 返回 HTTP 501 和 NOT_IMPLEMENTED。它们不证明真实模型或数据库可用。

## 其他文档何时看

架构变化查 [ARCHITECTURE](docs/ARCHITECTURE.md)，接口变化查 [CONTRACTS](docs/CONTRACTS.md)，需要知道设计原因查 [DECISIONS](docs/DECISIONS.md)。[ACCEPTANCE](docs/ACCEPTANCE.md) 保存跨模块测试场景；[SOURCE_MAP](docs/SOURCE_MAP.md) 对照 PDF 页码；[RISKS](docs/RISKS.md) 只在相关风险出现时查。[handoffs](handoffs) 是已经完成模块的真实证据，不必每次全部阅读。

原 PDF 放在本机 `docs/`，由 Git 忽略；原件、截图、内部地址、真实客户标识和凭据不提交公共仓库。
