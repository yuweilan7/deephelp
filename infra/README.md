# DeepHelp 云端中间件实验环境

部署：LOCAL APP + REMOTE MIDDLEWARE。P00 实际验收结果见 [validation.md](reports/validation.md)，完整连接/运维说明见 [connections.md](reports/connections.md)，恢复与暂停见 [rollback.md](reports/rollback.md)。

当前入口是本仓库的 `infra/`，不再要求从旧的独立镜像目录运行。凭据加载、接手机器和模型额度位置见 [本地启动与依赖交接](../docs/LOCAL_SETUP.md)。密码和私钥由本地配置提供，真实值不提交公共仓库。

| 服务 | 固定版本 | 内存上限 | CPU 上限 |
|---|---|---|---|
| MySQL | 8.4.11 | 512 MiB | 0.5 |
| Redis | 8.2.8-alpine | 128 MiB（maxmemory 64 MiB） | 0.25 |
| Milvus Standalone Embedded | 2.6.23 | 5 GiB | 2.75 |

精确 digest 和主机实测信息见 `reports/environment.lock.json`。Milvus 使用 embedded etcd + 本地 Woodpecker WAL，没有外部 MQ、MinIO 或 Milvus Lite。数据盘是独立 bind 目录，采用 `SOFT_GUARD`，未创建 loop 文件系统或硬配额。

## 本机开始

```powershell
# 在 deephelp 仓库根目录执行；首次使用时创建本地客户端环境。
py -3.11 -m uv venv --python 3.14.7 infra/.venv314
py -3.11 -m uv pip install --python infra/.venv314/Scripts/python.exe --requirement infra/client/requirements.lock.txt
.\infra\client\tunnel.ps1 -Action start
.\infra\client\tunnel.ps1 -Action health
```

`health` 使用应用账号做 MySQL SELECT、Redis PING、Milvus 版本/数据库/集合列表读取；新报告写入被 Git 忽略的 `.local/infra-health/client-health.json`，不覆盖历史 P00 报告。`check` 只检查隧道端口，不能替代鉴权检查。完成后可用 `.\infra\client\tunnel.ps1 -Action stop` 关闭本脚本管理的隧道。

运行完整合成验收可使用 `python infra/client/test-connections.py full`，它会重建本项目的 `p00_probe` 表内容和 `p00_acceptance` 集合，不用于真实业务数据，也不属于上述只读健康检查。`post-restart` 和 `recheck-persistence.py` 用于已有数据检查；`rebuild-milvus.py` 只新建 `p00_` 前缀集合。这些更深入操作按任务范围另行执行。

Windows 当前基线使用 `.venv314` 的 Python 3.14.7；保留旧虚拟环境仅用于复核历史验收证据。各虚拟环境均忽略入 Git。固定依赖见 `client/requirements.lock.txt`，可在新的本机 Python 3.14.7 venv 中安装。Linux/WSL 入口是 `bash client/tunnel.sh`。

## 重要范围

4 vCPU / 8 GB 主机仅供小数据、低并发实验。测试向量是确定性 4 维合成向量，不能据此评估 embedding 语义质量或生产容量。应用代码、模型 API、MCP 和训练全部留在本机。

当前模块进度以 [PROJECT_STATE](../docs/PROJECT_STATE.md) 为准，能力顺序以 [ROADMAP](../docs/ROADMAP.md) 为准。启动包旧描述中的 PostgreSQL、本机 Milvus 保底路线已被 P00 V2 的 MySQL + 云端 Milvus 明确要求覆盖，不自动沿用旧拓扑。

原始任务提示保留为 `reports/task-requirements.md`。官方版本依据为 Milvus v2.6.23 Release 与该 tag 的配置/脚本、MySQL 8.4 Release Notes、Redis 8.2.8 Release；具体来源和实际偏差均记入版本锁与验收报告。
