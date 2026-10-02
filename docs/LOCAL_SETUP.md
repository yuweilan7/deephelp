# 本地启动与依赖交接

这是运行环境入口。当前应用为 M01 离线骨架；中间件可连接、模型目录可读不代表业务联调完成。进度见 [PROJECT_STATE](PROJECT_STATE.md)。

## 本机配置与新机器接手

同机先读 `.local/DEPENDENCIES.md`：凭据路径、实例、端口、最近检查及恢复入口；机器索引为 `.local/dependency-access.json`。两者被 Git 忽略，不含秘密正文。

| 依赖 | 加载入口 | 新机器要提供 |
|---|---|---|
| 千问 | 根 `.env.local`，由 `uv run --env-file .env.local` 显式加载 | Key、对应 Base URL；字段见 [应用模板](../.env.example) |
| SSH / 中间件地址 | `infra/config/connections.env` | 实际主机、账号、端口和私钥路径；见 [连接模板](../infra/config/connections.env.example) |
| MySQL / Redis / Milvus 密码 | `DEEPHELP_SECRET_FILE` 指定文件，默认 `~/.deephelp/.env.secret` | 与既有服务一致的应用及管理密码；见 [密码模板](../infra/config/secrets.env.example) |
| SSH 主机身份 | 本机 `infra/client/known_hosts` | 经独立核对的目标 host key；保持严格校验 |
| 腾讯云管理 API | 当前运行不使用 | 新增云管理任务时另配 SecretId / SecretKey |

复制模板为真实配置，凭据通过已有安全渠道交付。可将密码和私钥放在项目 `.secrets/`，修改私钥路径，并在运行探针的同一 PowerShell 会话设置 `$env:DEEPHELP_SECRET_FILE = Join-Path (Get-Location) '.secrets/middleware.env'`。既有凭据无需搬动。`.env.local`、`.secrets/`、`.local/`、私钥、连接配置和 known_hosts 都被忽略；克隆公共仓库不会取得这些文件。`infra/.env` 仅配置镜像版本。

## Windows：在仓库根目录执行

Python 为 3.14.7，应用依赖由根 uv workspace / 单一 `uv.lock` 管理。根 `.python-version` 固定 3.14.7，根 pyproject 要求 uv `>=0.12.13,<0.13`。Windows 的 Python 启动器入口统一为 `py -3.14`；先确认解释器和 uv：

```powershell
py -3.14 --version
py -3.14 -m uv --version
# 仅 uv 缺失或低于项目要求时安装/升级；本机已验证 0.12.13。
py -3.14 -m pip install --upgrade "uv==0.12.13"
```

首次准备和启动离线应用：

```powershell
py -3.14 -m uv sync --locked --all-packages
py -3.14 -m uv run --locked --env-file .env.local uvicorn deephelp_app.app:create_app --factory --host 127.0.0.1 --port 8000
```

配置只从环境读取，不自动发现 dotenv。默认 dev/test 使用 fake；`/converse` 返回占位结果。配置 Key 不会开启真实模型调用，接口边界见 [应用说明](../modules/deephelp-app/README.md)。

解释器核验：`py -3.14 -m uv run --locked python -c "import sys; assert sys.version_info[:3] == (3, 14, 7); print(sys.executable)"`。旧启动入口的历史验证原文保留 Git 历史，当前运行命令统一使用 3.14.7。uv 为独立工具；版本门禁不会降低应用的 Python 要求。

中间件客户端使用单独的既有 P00 环境，不新增根锁。首次安装后复用即可：

```powershell
py -3.14 -m uv venv --python 3.14.7 infra/.venv314
py -3.14 -m uv pip install --python infra/.venv314/Scripts/python.exe --requirement infra/client/requirements.lock.txt
.\infra\client\tunnel.ps1 -Action start
.\infra\client\tunnel.ps1 -Action health
```

`health` 做 MySQL SELECT、Redis PING、Milvus 版本/数据库/集合列表读取，报告为 `.local/infra-health/client-health.json`。`check` 只测端口；用 `-Action stop` 关闭本脚本管理的隧道。POSIX 入口、备份和恢复见 [infra](../infra/README.md) 与 [运维说明](../infra/OPERATIONS.md)。

## 模型记录按需读取

先读 `.local/model-pool-summary.md`，再查询 `.local/model-pool.json` 中所需候选；原始目录/额度/用量快照由该索引引用。核对快照时间、到期时间和当前账户额度；已验证范围见 PROJECT_STATE，不将两个候选的实测外推到全部目录。

候选保留额度状态及观察时间；模型使用遵循 [AGENTS](../AGENTS.md)。免费额度用完可换用能力合适的候选，也可使用账号余额继续调用；当前代码尚未实现自动切换，须显式更新模型配置并完成对应真实验收。Embedding签名变化必须新版本集合与重建，不能将同维模型静默混入旧集合。目录和额度快照不能替代实际可调用性与能力验证。

## M03 受控真实验收

配置模板为 [providers.example.json](../modules/deephelp-app/providers.example.json)，能力及兼容规则见 [模型矩阵](MODEL_CAPABILITIES.md)。凭据只通过环境变量读取，chat/embed可分别配置；调用前核验鉴权、可调用性和所需能力。脚本不修改账户开关，也不自动选择替代模型。

运行上限由当前任务设置，覆盖必要输入、输出、诊断与复验。脚本仍要求调用/token/费用参数及.local累计文件，用于防止失控调用；恢复执行沿用原累计记录，不归零。字段和历史报告留.local，具体金额与用量不复制到常驻说明。先查看参数，再设置本次验证的`$probeBudgetFile`、`$probeReportFile`、`$probeCalls`、`$probeTokens`和`$probeCost`：

`$probeBudgetFile`须指向已初始化的.local累计文件：任务上限字段为`max_calls`、`max_tokens`、`max_cost_cny`，计数为`attempts`、`tokens`、`charged_tokens`，并保留`cost_upper_cny`、`uncertain_attempts`及`stages`。新任务从零初始化计数；恢复已有任务不得清零。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.live_probe --help
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.live_probe --live --stage feature --budget-state $probeBudgetFile --output $probeReportFile --max-calls $probeCalls --max-tokens $probeTokens --max-cost $probeCost
```

feature 分别检查四项能力，main 对无代码变化的合并进行 chat/embed 最小复验。共享预算在实际发请求前落盘，单进程文件锁阻止并发穿透；超时/用量未知保留预留占用。live 重试为 0，默认 pytest 仍离线；pytest 的 `--live` 只是配置检查，真实验收使用上述独立入口。模型响应结构诊断只记录 usage、维度、索引、工具名和安全请求 ID，不记录 prompt、正文、参数、凭据或隐藏推理。
