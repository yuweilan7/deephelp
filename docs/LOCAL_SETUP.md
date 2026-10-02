# 本地启动与依赖交接

本文件是运行环境的固定入口。当前代码只完成 M01 离线骨架；模型目录可读、中间件可连接，不等于 M03/M05 或业务联调已经完成。实时状态见 [PROJECT_STATE](PROJECT_STATE.md)。

## 同一台机器接手

先读仓库根目录下的 `.local/DEPENDENCIES.md`。它记录本机完整路径、实例标识、端口、最新实际检查结果和恢复命令；`.local/dependency-access.json` 保存对应机器可读索引。两者由 Git 忽略，不包含密码或私钥正文。

模型完整记录位于 `.local/model-pool.json`，原始模型目录、账户额度和用量快照也由该文件引用。检查前读取快照时间、额度状态和到期时间；再次调用前重新查询当前账户，不能直接把历史 remaining 当作当前可用额度。账户额度与某个 API Key 的抵扣归属还需要受控推理核对。

## 凭据在哪里加载

| 依赖 | 配置入口 | 新机器需要提供什么 |
|---|---|---|
| 千问模型 | 根 `.env.local`；显式使用 `uv run --env-file .env.local` | `DEEPHELP_MODEL_API_KEY` 和对应 `DEEPHELP_MODEL_BASE_URL` |
| 中间件地址、账号及 SSH | `infra/config/connections.env` | 实际主机、用户、端口和 `DEEPHELP_SSH_KEY_WINDOWS` 指向的私钥文件 |
| MySQL / Redis / Milvus 密码 | 默认 `~/.deephelp/.env.secret`；可由 `DEEPHELP_SECRET_FILE` 指向别的文件 | 与现有服务一致的应用及管理员密码；字段见 [密码文件模板](../infra/config/secrets.env.example) |
| SSH 主机身份 | `infra/client/known_hosts` | 与目标主机核对后的 host key；不通过关闭检查绕过主机身份验证 |
| 腾讯云管理 API | 当前运行流程不使用 | 只有新增云资源管理任务才需要 SecretId / SecretKey |

`.env.example` 和 `connections.env.example` 是模板。`infra/.env` 是镜像版本配置，不是客户端密码来源。本机实际位置以私有索引为准；现有凭据不必搬动。

在新机器可以把中间件密码放到项目附近的 `.secrets/middleware.env`，并在执行健康检查的同一个 PowerShell 会话中指定：

```powershell
$env:DEEPHELP_SECRET_FILE = Join-Path (Get-Location) '.secrets/middleware.env'
```

私钥同样可以放到 `.secrets/`，然后修改本机 `infra/config/connections.env` 的私钥路径。真实文件通过已有安全渠道交付；仅克隆公共仓库不会自动取得凭据。根 `.gitignore` 已排除 `.env.local`、`.secrets/`、`.local/`、私钥和本机连接配置。

## Windows 启动和只读检查

以下在仓库根目录执行。Python 3.14.7 和根 uv workspace 保持不变；`infra/.venv314` 只是既有 P00 客户端依赖的本地环境，使用其原有固定 requirements，不新增根锁文件。

```powershell
# 首次准备；已有该环境时无需重新创建。
py -3.11 -m uv venv --python 3.14.7 infra/.venv314
py -3.11 -m uv pip install --python infra/.venv314/Scripts/python.exe --requirement infra/client/requirements.lock.txt

# 保留后台 SSH 隧道，再验证实际协议和应用账号鉴权。
.\infra\client\tunnel.ps1 -Action start
.\infra\client\tunnel.ps1 -Action health

# 不再需要连接时关闭本脚本管理的隧道。
.\infra\client\tunnel.ps1 -Action stop
```

`health` 只执行 MySQL SELECT、Redis PING，以及 Milvus 版本/数据库/集合列表读取。报告保存到 `.local/infra-health/client-health.json`；失败报告包含诊断信息，仅留本地。它不建表、不写缓存、不重建集合，不运行 P00 full。隧道 PID 和日志位于 `infra/client/.tunnel-windows.json` 与 `.tunnel-windows.log`。

启动离线应用仍使用根 workspace：

```powershell
py -3.11 -m uv sync --locked --all-packages
py -3.11 -m uv run --locked --env-file .env.local uvicorn deephelp_app.app:create_app --factory --host 127.0.0.1 --port 8000
```

当前默认 dev/test 使用 fake；配置 Key 和打开隧道不会让 M01 自动获得真实业务能力。`/converse` 尚未实现。

## 模型额度与候选池

`.local/model-pool.json` 保留全部模型目录与额度记录的索引，同时列出 chat、embedding、rerank 候选池。失效模型保留历史条目，并分别记录 `expired`、`exhausted`、鉴权错误或能力不支持的原因和观察时间，不删除后重新误选。

本次只保存候选和操作约定，自动换模型代码尚未实现。后续 M03 可在明确预算内为 chat 建立经过 chat/schema/tool 实测的候选池；额度耗尽时记录状态，再从池中选择实时有资格的下一项。必须限制切换次数和总预算、记录实际 model 与原因，所有候选不可用时停止；普通 429 限流、鉴权错误和网络故障不能直接标成额度耗尽。资格不明时不能自动转为付费。

Embedding 固定 provider/model/revision/dimension/normalization 签名。即使两个模型都是 1024 维，也不允许静默切换后写入同一 Milvus collection；需要新版本集合和重建索引。Rerank 也单独验证能力与输入限制。

上述记录不覆盖 [M03 接入与验收要求](MODULES/M03_MODEL_GATEWAY.md)：缺少预算或真实能力时停止依赖它的特性，不以目录列表、免费额度或 Mock 代替真实验收。
