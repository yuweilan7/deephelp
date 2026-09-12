# DeepHelp 云端中间件实验环境

部署：LOCAL APP + REMOTE MIDDLEWARE。P00 实际验收结果见 [validation.md](reports/validation.md)，完整连接/运维说明见 [connections.md](reports/connections.md)，恢复与暂停见 [rollback.md](reports/rollback.md)。

服务器交付目录 `/srv/deephelp-infra`；本机镜像目录 `D:\IdeaProject\deephelp-infra`。密码未存入本仓库，SSH 私钥也不在仓库内。

| 服务 | 固定版本 | 内存上限 | CPU 上限 |
|---|---|---|---|
| MySQL | 8.4.11 | 512 MiB | 0.5 |
| Redis | 8.2.8-alpine | 128 MiB（maxmemory 64 MiB） | 0.25 |
| Milvus Standalone Embedded | 2.6.23 | 5 GiB | 2.75 |

精确 digest 和主机实测信息见 `reports/environment.lock.json`。Milvus 使用 embedded etcd + 本地 Woodpecker WAL，没有外部 MQ、MinIO 或 Milvus Lite。数据盘是独立 bind 目录，采用 `SOFT_GUARD`，未创建 loop 文件系统或硬配额。

## 本机开始

```powershell
cd D:\IdeaProject\deephelp-infra
.\client\tunnel.ps1 -Action start
.\client\tunnel.ps1 -Action health
.\.venv312\Scripts\python.exe .\client\recheck-persistence.py
```

运行完整合成验收可使用 `python client/test-connections.py full`，它会重建本项目的 `p00_probe` 表内容和 `p00_acceptance` 集合，不用于真实业务数据。`post-restart`、`health` 和 `recheck-persistence.py` 用于已有数据检查。`rebuild-milvus.py` 只新建 `p00_` 前缀集合。

Windows 优先运行 `.venv312` 的 Python 3.12；保留初始 `.venv` 的基础设施验收环境用于复核。两者均忽略入 Git。固定依赖见 `client/requirements.lock.txt`，可在新的本机 Python 3.12 venv 中安装。Linux/WSL 入口是 `bash client/tunnel.sh`。

## 重要范围

4 vCPU / 8 GB 主机仅供小数据、低并发实验。测试向量是确定性 4 维合成向量，不能据此评估 embedding 语义质量或生产容量。应用代码、模型 API、MCP 和训练全部留在本机。

下一步按照用户启动包进入 **M00_ARCHITECTURE**；已有架构章程完成后再进入 **M01_PYTHON_ASYNC_SKELETON**。启动包旧描述中的 PostgreSQL、本机 Milvus 保底路线已被本轮 P00 V2 的 MySQL + 云端 Milvus 明确要求覆盖，不自动沿用旧拓扑。

原始任务提示保留为 `reports/task-requirements.md`。官方版本依据为 Milvus v2.6.23 Release 与该 tag 的配置/脚本、MySQL 8.4 Release Notes、Redis 8.2.8 Release；具体来源和实际偏差均记入版本锁与验收报告。
