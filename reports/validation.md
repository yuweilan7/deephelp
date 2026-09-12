# P00 V2 实际验收

状态：**DONE / REMOTE_CLIENT_VALIDATED**。Milvus 云端接受范围：**低数据、低并发实验**。

## 验收结果

| 项目 | 结果 | 证据 |
|---|---|---|
| MySQL 8.4.11 | RUNNING + HEALTHY；中文/emoji、commit/rollback、唯一键、索引、UTC、20 全局连接/16 业务用户连接上限、权限隔离通过 | client-full.json / server-smoke.log |
| Redis 8.2.8 | RUNNING + HEALTHY；ACL、TTL 到期、64 MiB maxmemory、allkeys-lru、关闭 AOF/RDB 通过 | client-full.json |
| Milvus 2.6.23 | RUNNING + HEALTHY；Chinese Analyzer、BM25 Function、Sparse/Dense、WeightedRanker hybrid、filter、insert/delete 全部通过 | client-full.json |
| 认证负向测试 | MySQL 错密码、Redis 无认证、Milvus 无认证和越权管理请求均拒绝；Milvus root 默认/初始密码已失效 | auth-negative-tests.json / milvus-rbac.json |
| 重启 | MySQL 中文行仍在；Milvus 36 行仍在且被删 id=999 不存在；Redis 缓存丢失符合预期 | client-post-restart.json / observation-queries.jsonl |
| 备份恢复 | MySQL dump→新隔离测试库→查询中文通过；Milvus dataset/schema→新集合 36 行→BM25 通过 | mysql-backup-restore.log / milvus-rebuild.json |
| 本机 | Windows OpenSSH 隧道实际运行，停止/重连/health 已测试；Python 3.12.14 健康和持久化 hybrid 查询通过 | client-health.json / observation-queries.jsonl |
| 公网隔离 | Docker 实际仅发布回环；云防火墙无 DB/UI 入站；本机直连无服务协议响应 | infra_after.json / cloud-firewall.json / public-port-check.json |
| WebUI | Chrome 打开本机隧道 URL 并渲染页面；首页详情表 No Data，未宣称完整遥测显示成功 | 浏览器标签 Milvus Management；webui=PAGE_LOADED_DETAILS_EMPTY |
| Linux/WSL | 脚本已生成且 bash -n 通过；本机无可用 WSL 发行版，未在 WSL 实际执行 | client/tunnel.sh |
| 开机/健康/日志 | systemd 开机启用、先 core 后 Milvus；healthcheck 与有界日志 inspect 通过；本轮未重启整个云主机 | server-smoke.log / environment.lock.json |

## 12 分钟资源观察

连续 720.0 秒，24 个样本，约每 30 秒采样。所有样本 healthy；OOM=0，自动重启=0；swap in/out 增量均为 0。宿主 MemAvailable 最低 **6.42 GiB**，根盘可用最低 **48.04 GiB**。

| 服务 | 观察末 cgroup memory.current | cgroup memory.peak | 内存上限 |
|---|---|---|---|
| MySQL | 189.3 MiB | 196.5 MiB | 512 MiB |
| Redis | 4.5 MiB | 7.2 MiB | 128 MiB |
| Milvus | 185.8 MiB | 188.5 MiB | 5120 MiB |

原始 CPU、RSS、memory.current/peak、swap、磁盘、健康、restart/OOM 数据在 stability.jsonl。最后宿主内存、磁盘和数据目录大小在 infra_after.json。Docker 生命周期事件及 start→first healthy 观测在 acceptance-summary.json；该时间包含健康检查采样间隔，并非内部初始化的精确耗时。

首次客户端 Dense/BM25/Hybrid 查询耗时分别约 134.9 / 138.4 / 158.5 ms，包含 Windows 客户端、SSH 与网络开销；不是压测结论。

## 实施中发现并修正的差异

1. 官方 MySQL Release Notes 已列 8.4.12，但官方 Docker 标签不存在。固定可获取的 8.4.11，已与官方仓库 digest 对照。
2. Milvus v2.6.23 tag 内的 standalone_embed.sh 仍写 v2.6.22，且默认全地址发布并额外发布 etcd。保留原脚本与 SHA256，只参照其嵌入式模式；项目 Compose 修正版本、回环发布，不发布 2379，保留 Docker 默认 seccomp。
3. 初次 internal bridge 使 Docker 29 未建立 host 映射：容器内 healthy 但本机连接失败。只重建项目容器/网络，改为普通 bridge + 显式回环发布；后续全部本机验收通过。旧失败记录保留在 pull/startup 日志与本报告，没有伪称首次即成功。
4. 本机 TCP connect 对被阻断端口和未使用的对照端口也可先返回成功，随后 EOF；因此记录应用协议探测与云防火墙/绑定证据，不把单次 connect 返回值冒充公开服务可达。
5. 初始验收使用已有 Python 3.11；随后创建 Python 3.12.14 venv，再次完成认证与持久化混合查询。uv 首次 minor-link 建立报错后改用已经下载的明确解释器路径创建 venv，没有改动系统 Python。

## 边界与下一步

没有业务代码上云，没有购买服务、修改套餐、暴露数据库或改弱 SSH。没有硬配额，DISK_LIMIT_TYPE=SOFT_GUARD。37 条插入/1 条删除以及 36 条重建为合成小数据测试，确定性向量不能验证语义质量。

按照启动包先进入 **M00_ARCHITECTURE**，架构章程完成后进入 **M01_PYTHON_ASYNC_SKELETON**。应用保持本机运行，MySQL 为业务事实来源；LangGraph 暂用本机 SQLite/AsyncSqliteSaver，M15 再评估 checkpoint 持久化；M19 才涉及应用部署。
