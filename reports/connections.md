# DeepHelp P00 连接说明

拓扑：本机 Python 应用 → SSH Tunnel → 云端 MySQL / Redis / Milvus。服务器不运行 FastAPI、Agent、MCP、FastText 或业务应用。

服务器实例 `lhins-56mgod8p`，广州；公网 `134.175.142.187`，未配置 DNS；SSH `ubuntu`，端口 `22`，保持密钥认证，未修改 SSH 策略。实例原有 SSH 密钥已复用。

| 服务 | 服务器发布地址 | Windows 本机地址 | 日常用户/库 |
|---|---|---|---|
| MySQL 8.4.11 | 127.0.0.1:3306 | 127.0.0.1:13306 | deephelp_app / deephelp |
| Redis 8.2.8 | 127.0.0.1:6379 | 127.0.0.1:16379 | deephelp_app / 0，键名前缀 deephelp: |
| Milvus 2.6.23 | 127.0.0.1:19530 | http://127.0.0.1:19530 | deephelp_app / deephelp |
| Milvus WebUI | 127.0.0.1:9091 | http://127.0.0.1:19091/webui/ | SSH 隧道保护 |

本机端点统一读取 `config/connections.env`（若无则读取 `.example`）。初始四端口均空闲，未改用其他端口。改变 IP 或端口时只修改该配置，同时同步 URI 字段。脚本启动前检测占用并报错，不抢占其他进程。

## Windows PowerShell

```powershell
cd D:\IdeaProject\deephelp-infra
.\client\tunnel.ps1 -Action start
.\client\tunnel.ps1 -Action check
.\client\tunnel.ps1 -Action health
.\client\tunnel.ps1 -Action restart
.\client\tunnel.ps1 -Action stop
```

`start` 在后台启动本机 OpenSSH。`restart` 只停止 PID 文件对应且命令行匹配的本项目隧道。`health` 实际认证三个中间件。已验证启动、停止/重连、认证健康与完整业务功能。隧道当前保留运行。

## Linux / WSL

```bash
cd /path/to/deephelp-infra
bash client/tunnel.sh start
bash client/tunnel.sh check
DEEPHELP_SECRET_FILE="$HOME/.deephelp/.env.secret" \
  DEEPHELP_PYTHON=/path/to/python bash client/tunnel.sh health
bash client/tunnel.sh restart
bash client/tunnel.sh stop
```

WSL 可使用 `/mnt/d/IdeaProject/deephelp-infra`。脚本首次运行时可把已经验证的 Windows 私钥复制到 WSL 的 `$HOME/.ssh/deephelp_lighthouse.pem` 并设为 600；不生成新云凭据。普通 Linux 请把同一私钥安全复制到该位置，或设置 `DEEPHELP_SSH_KEY`。主机公钥使用交付的 `client/known_hosts`，严格校验。秘密 env 也需安全复制到该 Linux 用户目录并 `chmod 600`。本机没有可用 WSL 发行版，因此 POSIX 脚本完成语法检查，未在 WSL 中实际建立隧道；Windows 客户端已实际验收。

隧道均使用 `ExitOnForwardFailure=yes`、`ServerAliveInterval=30`、`ServerAliveCountMax=3`，掉线后用 `restart` 重连。未安装 autossh。

## Python 使用

安全本机秘密文件：`C:\Users\Lenovo\.deephelp\.env.secret`（Windows ACL 仅当前用户）。服务器文件：`/srv/deephelp-infra/.env.secret`，权限 600。SSH 私钥：`C:\Users\Lenovo\.ssh\deephelp_lighthouse.pem`，未放项目目录。

连接模板：`config/connections.env.example`。测试程序用 `client/settings.py` 读取配置与秘密文件，应用只需使用 MYSQL_PASSWORD、REDIS_PASSWORD、MILVUS_PASSWORD 三个业务秘密。root/admin 仅用于管理。无需在聊天或 Git 中填写密码。

建议将结构化参数传给 Python 驱动；如使用 DSN，必须编码密码：

```python
from urllib.parse import quote
mysql_dsn = f"mysql+aiomysql://{user}:{quote(password, safe='')}@{host}:{port}/deephelp?charset=utf8mb4"
redis_url = f"redis://deephelp_app:{quote(redis_password, safe='')}@127.0.0.1:16379/0"
# MilvusClient(uri=uri, user='deephelp_app', password=milvus_password, db_name='deephelp')
```

依赖锁定见 `client/requirements.lock.txt`；PyMilvus 推荐/验收版本为 2.6.17。确定性 4 维向量只验证接口正确性，不证明 embedding 语义质量。

## 服务运维

在服务器 `/srv/deephelp-infra` 下使用 `sudo bash scripts/start-core.sh`、`stop-core.sh`、`restart-core.sh`、`start-milvus.sh`、`stop-milvus.sh`、`doctor.sh`、`smoke-infra.sh`。`compose.sh` 私下加载秘密，禁止把 `docker compose config` 或完整容器环境输出到公开日志。

systemd `deephelp-infra.service` 开机先等待 MySQL、Redis healthy，再启动 Milvus 并等待 healthy。Milvus 容器最多重试 3 次；启动服务失败时 60 秒退避、900 秒内最多 3 次。unhealthy 状态由 doctor 报告，不用高频重启掩盖故障。

数据目录：`/srv/deephelp-data/{mysql,redis,milvus}`；备份：`/srv/deephelp-backup`；交付：`/srv/deephelp-infra`。Redis 的目录存在但 AOF/RDB 均关闭，丢失是预期行为。

磁盘为 `SOFT_GUARD`，无硬配额。新增 pull/import/build/backup 之前运行 `sudo bash scripts/disk-guard.sh ACTION`。Milvus bulk insert 保持单并发、小 batch；不要直接绕过 guard 灌入大数据。
