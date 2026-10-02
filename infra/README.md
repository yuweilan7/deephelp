# 云端中间件

拓扑为本机应用 → SSH 隧道 → 云端 MySQL / Redis / Milvus。配置和 Windows 启动只维护在 [LOCAL_SETUP](../docs/LOCAL_SETUP.md)，备份、恢复和服务器操作见 [OPERATIONS](OPERATIONS.md)，历史验收边界见 [P00 handoff](../handoffs/P00.md)。

| 服务 | 固定版本 | 内存 / CPU 上限 | 云端 loopback → 本机 loopback |
|---|---|---|---|
| MySQL | 8.4.11 | 512 MiB / 0.5 | 3306 → 13306 |
| Redis | 8.2.8-alpine | 128 MiB / 0.25；maxmemory 64 MiB | 6379 → 16379 |
| Milvus Standalone Embedded | 2.6.23 | 5 GiB / 2.75 | 19530 → 19530；WebUI 9091 → 19091 |

镜像 tag/digest 在服务器 `infra/.env`，客户端依赖锁在 [requirements.lock.txt](client/requirements.lock.txt)。Milvus 使用 embedded etcd / 本地 Woodpecker WAL，数据为独立 bind 目录；磁盘采用 SOFT_GUARD。4 核 / 8 GB 环境用于小数据、低并发实验；4 维合成向量只证明接口能力。

## 客户端脚本

| 入口 | 用途与边界 |
|---|---|
| `client/tunnel.ps1` / `client/tunnel.sh` | 管理专属隧道；health 实际认证，check 仅测端口 |
| `client/test-connections.py health` | 只读健康检查；Windows/POSIX 隧道入口保存报告到 `.local/infra-health/` |
| `client/test-connections.py full` | 重建 p00 探针表内容/集合、写缓存；仅授权的合成验收使用 |
| `client/test-connections.py post-restart` / `recheck-persistence.py` | 检查已有 P00 持久数据；前者也会确保探针表存在 |
| `client/rebuild-milvus.py` | 按唯一 schema 与 dataset 新建 p00_ 合成集合，不覆盖已有集合 |
| `client/security-check.py` / `doctor.py` | 专用安全检查 / 资源诊断；安全检查会尝试拒绝路径，不是纯读健康探针 |

保留的服务器脚本用途见 OPERATIONS。新生成的报告被 Git 忽略，[reports](reports/README.md) 只维护说明；旧报告、安装盘点和上游配置副本移到本机归档/Git 历史，不作为日常实施输入。

## Linux / WSL

在仓库根目录、配置/私钥/经核对的 known_hosts 已交付后执行：

```bash
bash infra/client/tunnel.sh start
DEEPHELP_PYTHON=/path/to/python3.14 bash infra/client/tunnel.sh health
bash infra/client/tunnel.sh stop
```

密码文件默认 `~/.deephelp/.env.secret` 或设 `DEEPHELP_SECRET_FILE`；私钥默认 `~/.ssh/deephelp_lighthouse.pem` 或设 `DEEPHELP_SSH_KEY`，权限 600。依赖使用同一 requirements 锁。WSL 可从连接配置中的 Windows 路径复制现有私钥，不能因此跳过主机身份核对。POSIX 入口仅做过语法验证，实际协议验收来自 Windows。
