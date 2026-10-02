# 中间件运维

这些命令在指定环境执行。日常只读 health 见 [LOCAL_SETUP](../docs/LOCAL_SETUP.md)；重启、恢复、删除和故障演练必须有对应任务授权。不要以容器 Up 代替协议鉴权，不重跑旧 P00 安装。

## 服务器入口

SSH 后进入 `/srv/deephelp-infra`。服务器运行中间件，不运行应用/Agent。业务账号为 deephelp_app、业务库为 deephelp；root/admin 只用于管理。MySQL、Redis、Milvus/WebUI 均发布到服务器 loopback，经客户端隧道访问。

| 脚本 | 用途 |
|---|---|
| `scripts/doctor.sh` / `smoke-infra.sh` | 资源/容器健康诊断、认证探针 |
| `scripts/compose.sh` | 私下加载 `.env.secret` 调用 Compose；不公开展开的配置/环境 |
| `scripts/start-core.sh` / `stop-core.sh` / `restart-core.sh` | MySQL / Redis 启停；启动等待 healthy |
| `scripts/start-all.sh` / `start-milvus.sh` / `stop-milvus.sh` | 完整顺序启动 / Milvus 启停；start-all 先等 core，start-milvus 检查磁盘 |
| `scripts/disk-guard.sh ACTION` | pull/import/build/backup 前的 SOFT_GUARD，非硬配额 |
| `scripts/pull-images.sh` | 拉取固定镜像并生成版本锁；不是日常启动步骤 |
| `scripts/backup-mysql.sh` / `restore-mysql.sh` | 事务一致性备份 / 独立库恢复 |
| `scripts/backup-restore-check.sh` | 创建恢复测试库并验证，不是只读探针 |

数据目录 `/srv/deephelp-data/{mysql,redis,milvus}`；备份 `/srv/deephelp-backup`。服务器秘密文件 `.env.secret` 权限 600，`compose.sh` 使用的 `.secrets/` 不交付公开仓库。

服务器现有 `deephelp-infra.service` 调用 start-all，先等待 core，再启动 Milvus；unit 安装记录留在 P00 历史，不是本仓库可重装模板。启动失败有退避与次数限制，unhealthy 由 doctor 报告；未做整机重启验收。暂停并保留数据：

```bash
sudo systemctl stop deephelp-infra.service
sudo systemctl disable deephelp-infra.service
sudo bash scripts/compose.sh stop -t 120
```

仅移除项目容器/网络时用 `compose.sh down`，保留 bind 数据。恢复用 `sudo systemctl enable --now deephelp-infra.service`，再运行服务器诊断和本机 health。销毁数据需单独明确范围，不使用全局 prune。

## 备份与重建

MySQL 为 InnoDB；备份期间避免 schema 变更。`sudo bash scripts/backup-mysql.sh` 在 guard 后生成权限 600 的 gzip。`sudo bash scripts/restore-mysql.sh /srv/deephelp-backup/FILE.sql.gz deephelp_restore_NAME` 只创建独立恢复库，拒绝覆盖已有库。

Milvus 合成重建在本机、隧道开启时从仓库根运行 `infra/.venv314/Scripts/python.exe infra/client/rebuild-milvus.py --collection p00_rebuilt_NEWNAME`。唯一结构源为 `client/milvus_schema.py`，语料为 `client/dataset.json`；新建集合写入 36 条合成记录。它不是全库备份。真实资料另需保存授权原文、切分、向量签名和元数据；完整目录冷备须先正常停止 Milvus，运行时热拷贝不能宣称可靠备份。

Redis AOF/RDB 关闭，缓存丢失是预期行为；业务事实、幂等、审批和最终状态归 MySQL。MySQL/Redis/Milvus 的角色与 checkpoint 门禁见 [ARCHITECTURE](../docs/ARCHITECTURE.md)。
