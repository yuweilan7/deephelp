# 中间件运维

这些命令在指定环境执行。日常只读 health 见 [LOCAL_SETUP](../docs/LOCAL_SETUP.md)；现有DeepHelp专用云实验环境的重启、恢复、删除和故障演练已按[AGENTS](../AGENTS.md)授权，执行前核对目标与复验范围。不要以容器 Up 代替协议鉴权，不重跑旧 P00 安装。

## 服务器入口

SSH 后进入 `/srv/deephelp-infra`。服务器运行中间件，不运行应用/Agent。账号 deephelp_app 默认使用完整权限，方便单人学习与实验：MySQL 全局权限及授权能力、Redis 全命令/键/频道、Milvus 内置 admin 角色。保留已有密码与管理账号。MySQL、Redis、Milvus/WebUI 均发布到服务器 loopback，经客户端隧道访问。etcd 只在项目容器网络提供 Milvus 元数据，不发布主机端口。

| 脚本 | 用途 |
|---|---|
| `scripts/doctor.sh` / `smoke-infra.sh` | 资源/容器健康诊断、认证探针 |
| `scripts/compose.sh` | 私下加载 `.env.secret` 调用 Compose；不公开展开的配置/环境 |
| `scripts/start-core.sh` / `stop-core.sh` / `restart-core.sh` | MySQL / Redis 启停；启动等待 healthy |
| `scripts/start-all.sh` / `start-milvus.sh` / `stop-milvus.sh` | 完整顺序启动 / Milvus与etcd启停；start-all先等core，start-milvus检查磁盘并等etcd健康 |
| `scripts/disk-guard.sh ACTION` | pull/import/build/backup 前的 SOFT_GUARD，非硬配额 |
| `scripts/pull-images.sh` | 拉取固定镜像并生成版本锁；不是日常启动步骤 |
| `scripts/backup-mysql.sh` / `restore-mysql.sh` | 事务一致性备份 / 独立库恢复 |
| `scripts/backup-restore-check.sh` | 创建恢复测试库并验证，不是只读探针 |

数据目录 `/srv/deephelp-data/{mysql,redis,milvus}`；备份 `/srv/deephelp-backup`。服务器秘密文件 `.env.secret` 权限 600，`compose.sh` 使用的 `.secrets/` 不交付公开仓库。

服务器现有 `deephelp-infra.service` 调用 start-all，先等待 core，再按Compose依赖等待etcd、启动Milvus；unit安装记录留在P00历史，不是本仓库可重装模板。启动失败有退避与次数限制，unhealthy由doctor报告；整机重启验收另计。暂停并保留数据：

```bash
sudo systemctl stop deephelp-infra.service
sudo systemctl disable deephelp-infra.service
sudo bash scripts/compose.sh stop -t 120
```

仅移除项目容器/网络时用 `compose.sh down`，保留 bind 数据。恢复用 `sudo systemctl enable --now deephelp-infra.service`，再运行服务器诊断和本机health。删除数据前核对项目范围、引用和恢复来源；授权见AGENTS，不使用全局prune。

## 备份与重建

MySQL 为 InnoDB；备份期间避免 schema 变更。`sudo bash scripts/backup-mysql.sh` 在 guard 后生成权限 600 的 gzip。`sudo bash scripts/restore-mysql.sh /srv/deephelp-backup/FILE.sql.gz deephelp_restore_NAME` 只创建独立恢复库，拒绝覆盖已有库。

Milvus合成重建在本机、隧道开启时从仓库根运行 `infra/.venv314/Scripts/python.exe infra/client/rebuild-milvus.py --collection p00_rebuilt_NEWNAME`。唯一结构源为`client/milvus_schema.py`，语料为`client/dataset.json`；新建集合写入36条合成记录。它不是全库备份。真实资料另需保存授权原文、切分、向量签名和元数据；完整目录冷备须先正常停止Milvus与etcd（`stop-milvus.sh`），运行时热拷贝不能宣称可靠备份。

etcd3.5.23以256MiB/0.25CPU独立运行，复用`/srv/deephelp-data/milvus/etcd`；Milvus2.6.23以`ETCD_USE_EMBED=false`连接它，保留本地Woodpecker及原数据目录。原`embedEtcd.yaml`仅供回退参考，不能让嵌入和独立etcd同时写同一目录。维护重启使用`stop-milvus.sh`→`start-milvus.sh`，再doctor、本机health与M05只读verify；单独重启Milvus也应先检查etcd健康。回退前停两容器、冷备当前目录，按备份恢复匹配配置/数据后再验，禁止通过空库启动掩盖缺失记录。迁移及实际重启证据见[M05交接](../handoffs/M05.md)。

Redis AOF/RDB 关闭，缓存丢失是预期行为；业务事实、幂等、审批和最终状态归 MySQL。MySQL/Redis/Milvus 的角色与 checkpoint 门禁见 [ARCHITECTURE](../docs/ARCHITECTURE.md)。

## 学习环境权限

从本机仓库根运行；继续使用既有 P00 客户端与私下配置。`--apply` 更新已有环境并实际验收；省略它只验收，仍会创建并清理专用合成测试库/集合/缓存。输出必须为新的 .local 文件。

```powershell
infra/.venv314/Scripts/python.exe infra/client/learning-access.py --apply --output .local/learning-access/apply-new.json
infra/.venv314/Scripts/python.exe infra/client/learning-access.py --output .local/learning-access/verify-new.json
infra/.venv314/Scripts/python.exe infra/client/check-redis-acl.py --output .local/learning-access/redis-new.json
```

MySQL 取消账号专属连接/每小时配额，总连接数采用151；Redis ACL 为 `~* &* +@all`；Milvus 给应用账号授予 admin，已有角色保留。调整同时写服务器 MySQL 初始化SQL、mysql.cnf和Redis ACL文件，现有数据、密码与来源材料保留。MySQL/Milvus账号授权本身持久保存；Redis ACL LOAD即时生效，Redis重启后从同一文件加载。

MySQL旧授权/初始化SQL/配置及Redis原ACL保存在服务器专用备份目录，维护工具的私下报告记录具体位置。Redis ACL为单文件bind mount，工具原位写入并保留inode/权限；不能通过更换文件inode让运行容器继续读旧ACL。恢复Redis使用对应备份及 `update-redis-acl.py --restore`，该脚本位于仓库 infra/scripts；不要重新运行退役bootstrap覆盖当前学习配置。

测试实际覆盖脚本/CAS/事务/有序集合、MySQL视图/触发器/存储过程/19连接、Milvus独立库与内容检索。清库、关闭服务等权限通过ACL DRYRUN检查，不在权限验收中执行。中间件账号权限与应用的用户归属/版本/幂等规则分别维护。
