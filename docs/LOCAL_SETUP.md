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

Python 为 3.14.7，应用依赖由根 uv workspace / 单一 `uv.lock` 管理。首次准备和启动离线应用：

```powershell
py -3.11 -m uv sync --locked --all-packages
py -3.11 -m uv run --locked --env-file .env.local uvicorn deephelp_app.app:create_app --factory --host 127.0.0.1 --port 8000
```

配置只从环境读取，不自动发现 dotenv。默认 dev/test 使用 fake；`/converse` 返回占位结果。配置 Key 不会开启真实模型调用，接口边界见 [应用说明](../modules/deephelp-app/README.md)。

中间件客户端使用单独的既有 P00 环境，不新增根锁。首次安装后复用即可：

```powershell
py -3.11 -m uv venv --python 3.14.7 infra/.venv314
py -3.11 -m uv pip install --python infra/.venv314/Scripts/python.exe --requirement infra/client/requirements.lock.txt
.\infra\client\tunnel.ps1 -Action start
.\infra\client\tunnel.ps1 -Action health
```

`health` 做 MySQL SELECT、Redis PING、Milvus 版本/数据库/集合列表读取，报告为 `.local/infra-health/client-health.json`。`check` 只测端口；用 `-Action stop` 关闭本脚本管理的隧道。POSIX 入口、备份和恢复见 [infra](../infra/README.md) 与 [运维说明](../infra/OPERATIONS.md)。

## 模型记录按需读取

先读 `.local/model-pool-summary.md`，再查询 `.local/model-pool.json` 中所需候选；原始目录/额度/用量快照由该索引引用。核对快照时间、到期时间和当前账户额度；账户快照与某个 Key 的实际抵扣关系尚未受控推理验证。

候选保留耗尽/过期及观察时间，自动换模型尚未实现。额度耗尽、普通限流、鉴权和网络错误要区分；切换资格、次数及总预算由 [M03](MODULES/M03_MODEL_GATEWAY.md) 实测后约束。Embedding 不允许同维模型静默混入同一集合，签名变化必须新版本集合与重建。目录和额度快照不能替代真实能力验收。
