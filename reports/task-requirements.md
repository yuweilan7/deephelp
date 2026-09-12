# P00 V2｜Astra Execute Infra：初始化 DeepHelp 云端中间件实验环境

## 0. 本轮最终目标

你是本项目的基础设施实施工程师。

本轮不是纯规划，而是 EXECUTE_INFRA：

在已购云服务器上实际完成：

1. MySQL
2. Redis
3. Milvus Standalone

三项云端中间件的安装、配置、资源限制、安全设置、持久化、健康检查和远程开发连接配置。

应用业务代码本轮不部署到服务器。

DeepHelp 的 Python / FastAPI / LangGraph / MCP Mock / FastText / 测试代码，在后续开发阶段默认运行在用户个人电脑，通过 SSH Tunnel 访问云服务器上的 MySQL、Redis、Milvus。

因此本轮结束以后必须做到：

- 云服务器三个中间件能够真实运行；
- 数据重启后仍存在；
- 不把数据库和 Milvus 直接暴露给公网；
- 用户个人电脑能够通过 SSH Tunnel 实际访问三项服务；
- 输出可以直接复制进 Python 项目的连接配置；
- 输出 Windows PowerShell 和 Linux/WSL 两套 SSH Tunnel 启动方式；
- 输出所有必要用户名、数据库名、端口、连接 URI 模板；
- 密码本身只能保存在安全的本地秘密文件中，不在最终聊天结果中明文输出。

---

# 一、已知机器信息

云服务器：

- 地域：广州
- OS：Ubuntu 24.04
- 镜像名：Ubuntu24.04-Docker29 29.6.1
- CPU：4 vCPU
- RAM：8GB
- 系统盘：60GB SSD
- 月流量：500GB

镜像名称不视为真实版本检测结果。

用户电脑：

- 32GB RAM
- RTX 4060
- 主要开发环境可能是 Windows + WSL / VS Code / IDEA
- Python 应用、Agent 编排、模型 API、MCP Mock、训练与测试默认全部运行本机

本项目不会训练大模型。

---

# 二、授权和安全边界

先说明最小变更计划，再执行。

允许：

- 读取当前实例、Docker、磁盘、内存、网络、防火墙状态；
- 创建 /srv/deephelp 目录；
- 创建本项目 Docker network、volume/bind mount；
- 安装必要 CLI 和健康检查工具；
- 配置 MySQL / Redis / Milvus；
- 修改仅属于本项目的配置；
- 如果已经拥有云控制台权限，可以检查安全组；
- 创建防火墙规则时必须保护现有 SSH 通路。

禁止：

- 重装操作系统；
- 重分区；
- 格式化 /dev/*；
- 修改云套餐；
- 购买增值服务；
- 自动开启按量计费；
- 删除其他业务；
- docker system prune -a --volumes；
- 全局 rm -rf；
- 覆盖整个 daemon.json；
- 覆盖整个 fstab；
- 改弱 SSH 安全策略；
- 将 MySQL / Redis / Milvus / Milvus WebUI 直接暴露给 0.0.0.0；
- 使用来源不明的 Docker 镜像加速器；
- 输出密码/API Key；
- 上传 DeepHelp PDF 到公共仓库。

如果无法获得服务器 SSH 或控制台连接信息，只询问真正阻塞实施的字段，不猜测 IP。

---

# 三、第一步必须执行只读环境盘点

在做任何安装前收集：

- uname -a
- /etc/os-release
- lscpu
- CPU 架构
- SSE4.2 / AVX / AVX2 支持
- free -h
- /proc/meminfo
- swapon --show
- df -hT
- df -i
- lsblk
- findmnt
- /srv /var /var/lib/docker 空间
- Docker root dir
- containerd root
- docker version
- docker info
- docker compose version
- docker ps -a
- docker images
- docker volume ls
- docker system df
- ss -lntup
- UFW/nftables/iptables 状态
- SSH 实际端口
- 云安全组中现有入站端口（如果有控制台权限）

特别确认：

Milvus 要求 CPU 至少支持受支持的 SIMD 指令。

输出：

reports/infra_inventory.md
reports/infra_before.json

如果机器已有非本项目服务，建立 EXCLUSION LIST，本轮绝不能操作。

---

# 四、固定技术版本

## Milvus

优先使用当前稳定的 Milvus 2.6.x 补丁版本。

截至本启动方案编写时优先候选：

Milvus Server:
2.6.23

PyMilvus:
2.6.17

执行时重新查询官方 Release Notes 核对。

如果 2.6.x 已发布更新版本，不自动追新：

优先继续使用经过本 Prompt 明确验证的 2.6.23，
除非新补丁有明显必要修复，并记录升级理由。

镜像必须同时记录：

- tag
- digest
- release URL
- SHA256 / commit

禁止使用 latest。

采用：

Milvus Standalone Embedded 模式

优先使用对应 v2.6.23 tag 的 standalone_embed.sh，
不得直接执行 master 分支脚本。

Embedded 模式优先：

- Milvus 单容器
- embedded etcd
- 当前版本支持的本地 WAL/MQ

不得为了“架构看起来完整”额外部署 Kafka/Pulsar。

必须验证：

- 中文 Analyzer
- BM25 Function
- Sparse Vector
- Dense Vector
- hybrid_search
- WeightedRanker
- metadata filter
- insert
- delete
- restart persistence

Milvus Lite 不能代替上述验收。

## MySQL

使用 MySQL 8.4 LTS 的稳定补丁版本。

固定：

- Docker image tag
- image digest

不得使用 latest。

字符集：

utf8mb4

默认数据库：

deephelp

建立最小权限业务用户：

deephelp_app

root 账号不得作为 Python 项目日常连接账号。

建议：

innodb_buffer_pool_size 初始 128MiB
max_connections 初始 20

根据实际 RSS 允许小幅调整。

时间统一存 UTC。

## Redis

固定稳定版本和 digest。

Redis 仅承担：

- Cache
- TTL
- 临时状态
- 可重建数据

不承担：

- 唯一业务事实
- 审批结果
- 幂等事实
- Event / Question 最终状态

这些以后全部进入 MySQL。

Redis：

maxmemory = 64MiB 起步

容器整体内存上限与 maxmemory 必须区分。

采用适合缓存的 eviction policy。

默认关闭 AOF/RDB，
因为数据允许丢失并重建。

---

# 五、云服务器资源策略

这是最大风险项。

Milvus Standalone 官方最低 RAM 为 8GB，而整台服务器只有 8GB。

因此这是：

LOW-DATA / LOW-CONCURRENCY / EXPERIMENTAL deployment

不是官方推荐生产规格。

本轮必须真实验证，不得根据“容器 Up”判断成功。

起始 Docker 内存上限：

Milvus:
5GiB

MySQL:
512MiB

Redis:
128MiB

Milvus 如果发生 OOM：

只有在宿主机仍能稳定保留至少约 1.5GiB 系统可用空间的前提下，
允许进行一次调整，最高不超过约 5.5GiB。

禁止不断提高 Milvus memory limit 直到挤死系统。

MySQL / Redis 是低负载实验服务。

应用、FastAPI、LangGraph、MCP、FastText 不在服务器运行。

因此不再为 API/MCP/worker 在云机预留容器内存。

Milvus CPU：

最大约 2.5～3 CPU

MySQL：

约 0.5 CPU

Redis：

约 0.25 CPU

宿主系统始终保留调度余量。

Milvus：

- index build 单并发
- bulk insert 单并发
- 小 batch
- 不压测百万级数据
- 本项目主要测试架构正确性

---

# 六、磁盘策略：取消强制 loop 文件系统方案

本项目数据极少，60GB 系统盘最大的现实风险不是业务数据，而是：

- Docker image
- containerd content store
- Milvus index/WAL
- Docker log
- build cache
- 多版本镜像
- 临时下载

因此本轮不要默认创建五个 loop filesystem。

不要为了硬 quota 提高系统复杂度。

改为：

/srv/deephelp-data/mysql
/srv/deephelp-data/redis
/srv/deephelp-data/milvus
/srv/deephelp-backup
/srv/deephelp

每项独立目录。

软预算：

Milvus:
6GiB

MySQL:
2GiB

Redis:
256MiB

backup:
2GiB

项目 logs/artifacts:
1GiB

Docker image/content/build cache:
8GiB 准入预算

根盘目标：

始终 >= 12GiB free

告警：

root free < 12GiB:
WARNING

root free < 10GiB:
STOP NEW PULL/IMPORT

root free < 8GiB:
CRITICAL

Milvus data > 4.8GiB:
WARNING

MySQL > 1.6GiB:
WARNING

disk-guard 必须在：

- docker pull 前
- Milvus 大批量 insert 前
- build 前
- backup 前

执行。

只有当前文件系统原生、安全、无需重挂即可支持 project quota 时，
才可以额外配置硬 quota。

否则明确标记：

DISK_LIMIT_TYPE=SOFT_GUARD

不能伪称硬配额。

---

# 七、网络设计：中间件绝不直接暴露公网

这是本轮非常重要的要求。

服务器内部服务：

MySQL:
127.0.0.1:3306

Redis:
127.0.0.1:6379

Milvus:
127.0.0.1:19530

Milvus WebUI:
127.0.0.1:9091

Docker publish 必须显式绑定：

127.0.0.1:PORT:PORT

禁止：

0.0.0.0:3306
0.0.0.0:6379
0.0.0.0:19530
0.0.0.0:9091

因为 Docker port publishing 可能绕开普通 UFW 预期，
不能单纯依赖 UFW。

云安全组：

保留当前实际 SSH 端口。

确认以下端口没有公网入站规则：

3306
6379
19530
9091

不得为了“方便开发”全部开放公网。

如果已有这些公网规则，
先确认不是其他业务使用，
再删除属于本项目/误开的规则。

不得因此中断 SSH。

---

# 八、认证配置

## MySQL

创建：

database:
deephelp

user:
deephelp_app

只授予 deephelp database 所需权限。

随机生成高强度密码。

密码：

- 不输出到聊天
- 不进入 Git
- 写入 chmod 600 的 secret env

## Redis

启用密码/ACL。

使用 deephelp 项目独立凭据。

## Milvus

启用：

common.security.authorizationEnabled=true

不能继续使用默认：

root / Milvus

初始化后：

- 修改 root 默认密码
- 创建 deephelp_app 用户
- 日常 Python 使用 deephelp_app
- root 仅管理使用

仅授予项目需要权限。

---

# 九、SSH Tunnel：这是本地 Python 的默认访问方式

云中间件安装成功后，自动生成：

scripts/tunnel.sh
scripts/tunnel.ps1
config/connections.env.example

必须探测本机端口冲突。

首选本机端口：

MySQL:
13306 -> server 127.0.0.1:3306

Redis:
16379 -> server 127.0.0.1:6379

Milvus:
19530 -> server 127.0.0.1:19530

Milvus UI:
19091 -> server 127.0.0.1:9091

SSH 命令逻辑类似：

ssh -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 13306:127.0.0.1:3306 \
  -L 16379:127.0.0.1:6379 \
  -L 19530:127.0.0.1:19530 \
  -L 19091:127.0.0.1:9091 \
  USER@SERVER \
  -p SSH_PORT

不能把示例当作已验证命令。

最终脚本必须使用真实确认的：

SSH_USER
SSH_HOST
SSH_PORT

如果用户本机端口被占用：

自动选择其他高位端口，
并同步生成 connections.env。

Windows PowerShell 与 WSL/Linux 分别给出启动方法。

同时提供：

stop tunnel
check tunnel
health tunnel

方法。

---

# 十、必须返回给 Python 项目的连接参数

最终必须生成一个：

config/connections.env.example

格式至少包括：

# SSH
DEEPHELP_SSH_HOST=<actual-host>
DEEPHELP_SSH_PORT=<actual-port>
DEEPHELP_SSH_USER=<actual-user>

# MySQL via SSH tunnel
MYSQL_HOST=127.0.0.1
MYSQL_PORT=13306
MYSQL_DATABASE=deephelp
MYSQL_USER=deephelp_app
MYSQL_PASSWORD=<FILL_FROM_SECRET>

# async Python 示例
MYSQL_DSN=mysql+aiomysql://deephelp_app:<URL_ENCODED_PASSWORD>@127.0.0.1:13306/deephelp?charset=utf8mb4

# Redis
REDIS_HOST=127.0.0.1
REDIS_PORT=16379
REDIS_PASSWORD=<FILL_FROM_SECRET>
REDIS_URL=redis://:<URL_ENCODED_PASSWORD>@127.0.0.1:16379/0

# Milvus
MILVUS_URI=http://127.0.0.1:19530
MILVUS_USER=deephelp_app
MILVUS_PASSWORD=<FILL_FROM_SECRET>
MILVUS_TOKEN=<USER:PASSWORD>
MILVUS_DATABASE=deephelp

# Milvus Web UI
MILVUS_WEBUI=http://127.0.0.1:19091/webui/

注意：

密码含 @ : / ? # % 等字符时，
URI 必须进行 URL Encoding。

同时保留独立 HOST / PORT / USER / PASSWORD 变量，
Python 应优先使用结构化配置组装 DSN，
避免密码转义问题。

最终聊天回复不能输出真正密码。

---

# 十一、连接必须从“本机视角”真实验收

仅在服务器 curl localhost 不算完全通过。

如果 Computer Use 能操作用户电脑：

实际启动 SSH Tunnel。

然后从用户电脑执行：

MySQL:
SELECT VERSION();
SELECT DATABASE();
SELECT 1;

Redis:
PING
SET test
GET test
TTL test

Milvus:
list databases
create database/collection
insert
search
delete

WebUI:
浏览器访问
http://127.0.0.1:19091/webui/

如果不能操作用户电脑：

服务器侧测试完成后状态写：

SERVER_VALIDATED

SSH tunnel 配置写：

GENERATED

客户端验证写：

PENDING_LOCAL_VALIDATION

不得宣称 REMOTE_CLIENT_VALIDATED。

---

# 十二、Milvus 功能验收

创建极小的合成测试集合。

必须测试：

1. Collection creation
2. Chinese Analyzer
3. BM25 Function
4. Sparse search
5. Dense vector search
6. WeightedRanker hybrid_search
7. metadata filter
8. insert
9. delete
10. restart persistence

确定性向量可以验证接口，
不能声称验证了 embedding 语义质量。

测试规模：

几十到几百条即可。

本轮禁止为了证明性能灌百万数据。

记录：

- milvus container memory.current
- memory peak
- host MemAvailable
- swap
- CPU
- disk use
- startup time
- query time
- OOMKilled
- container restart count

至少进行一次：

10～15 分钟稳定运行观察。

以下条件失败：

- Milvus OOM
- 容器反复 restart
- 宿主 MemAvailable 长期 < ~1GiB
- 持续大量 swap
- root free <10GiB
- Milvus 核心能力无法完成

如果失败：

写：

MILVUS_CLOUD=NOT_ACCEPTED

并保留证据。

但本轮不要自动迁移 Milvus 到个人电脑。

Milvus 云端部署是当前明确目标。

失败后停止破坏性尝试，
给出：

- 失败原因
- 当前峰值
- 最小可行升级规格建议
- 是否可以通过减少其他服务/调整配置解决

等待用户决定。

---

# 十三、MySQL 实际验收

必须验证：

- utf8mb4 中文
- transaction commit
- rollback
- unique key
- index
- datetime/timezone
- restart persistence
- user permission isolation
- connection limit
- memory RSS

为后续项目预留表/schema 设计说明：

session
question
event
sop
tool_execution
idempotency_record
approval
audit_log

本轮不必建立完整业务表，
但要确认 MySQL 能承担上述数据。

LangGraph checkpoint 本轮不要强行塞 MySQL。

开发阶段默认：

SQLite / AsyncSqliteSaver

后续 M15 再评估：

- community MySQL checkpoint saver
- 自定义 BaseCheckpointSaver
- 或其他持久化方案

业务事实仍统一放 MySQL。

---

# 十四、Redis 实际验收

验证：

PING

SET with TTL

TTL expiration

maxmemory 设置

eviction policy

Redis restart

确认：

缓存丢失不会破坏业务正确性。

---

# 十五、SSH Tunnel 的可用性问题提前解决

实际开发中常见问题必须在 P00 解决：

## Tunnel 掉线

配置：

ServerAliveInterval
ServerAliveCountMax
ExitOnForwardFailure

给出重新连接脚本。

如果 WSL 环境可用且适合，
可选 autossh，
但不能强制安装。

## 本机端口冲突

doctor 脚本检测：

13306
16379
19530
19091

被占用则明确报错或选择替代端口。

## 云 IP 变化

记录实例：

instance id
public IP
public DNS（如有）

connections.env 与脚本只能读取一个统一配置，
不能把 IP 散落在几十个文件。

## SSH key

优先现有 key。

不得把 private key 放项目目录。

## 防火墙

doctor 同时验证：

公网扫描/远程连接不能直接访问：

3306
6379
19530
9091

而 SSH Tunnel 可以访问。

---

# 十六、Docker 日志和磁盘增长

新容器使用有界日志。

例如：

local driver

或：

json-file
max-size=10m
max-file=3

必须实际 inspect 确认。

不得修改既有容器的日志策略。

Milvus/MySQL 日志不得无限增长。

---

# 十七、服务启动策略

MySQL：
开机启动

Redis：
开机启动

Milvus：
因为它是核心云端依赖，本项目现在改为开机启动

但启动顺序和健康检查必须明确。

不能只依靠 restart: always 判断健康。

必须配置 healthcheck。

Milvus 启动失败不能疯狂高频 restart。

设置合理 backoff/重启次数或运维脚本。

---

# 十八、备份与恢复

因为是实验环境，不做复杂企业备份。

MySQL：

提供 mysqldump 脚本。

至少实际完成：

dump
创建测试 DB/表
restore
查询验证

Milvus：

提供针对当前 Standalone 部署形式可行的轻量备份方案。

不能一边服务运行一边直接复制不一致的数据目录并称作可靠备份。

如果官方完整 Milvus Backup 太重，
本项目至少要求：

原始测试 dataset + collection schema + rebuild script

能够重新构建向量库。

Redis 不备份。

---

# 十九、不要把项目代码部署到服务器

本阶段正式开发拓扑固定为：

LOCAL APP + REMOTE MIDDLEWARE

即：

用户电脑：
Python 3.12
uv/venv
FastAPI
LangGraph
LangChain components
MCP
FastText
pytest
Qwen API
Embedding API

服务器：
MySQL
Redis
Milvus

Python 本地运行：

uv run ...
pytest ...
uvicorn ...

直接通过 SSH Tunnel 使用远程数据。

本轮不：

- build 应用 Docker image
- push 应用镜像
- scp Python 源码
- 在服务器运行 FastAPI
- 在服务器跑 Agent
- 在服务器训练 FastText

等到后续生产化/部署模块 M19，
再单独练习：

local build
Docker image
deploy
healthcheck
rollback

避免把“开发 Agent”和“部署 Agent”耦合。

---

# 二十、生成这些交付物

工作目录：

/srv/deephelp-infra

至少生成：

compose.yaml
.env.example
.gitignore

config/
  mysql.cnf
  redis.conf
  milvus-user.yaml
  connections.env.example

scripts/
  doctor.sh
  start-core.sh
  stop-core.sh
  restart-core.sh
  start-milvus.sh
  stop-milvus.sh
  smoke-infra.sh
  disk-guard.sh
  backup-mysql.sh
  restore-mysql.sh

client/
  tunnel.sh
  tunnel.ps1
  test-connections.py

reports/
  infra_inventory.md
  infra_before.json
  infra_after.json
  resource_caps.csv
  environment.lock.json
  validation.md
  connections.md
  rollback.md

秘密文件：

.env.secret

权限：

chmod 600

不得提交 Git。

---

# 二十一、environment.lock.json 必须记录

OS

kernel

CPU model

SIMD

memory

filesystem

Docker version

Docker Compose version

Docker storage driver

containerd image store 状态

MySQL:
tag
digest
version

Redis:
tag
digest
version

Milvus:
server version
image digest
PyMilvus recommended version
standalone script source
script SHA256
embedded etcd config

监听地址

数据目录

日志策略

内存/CPU limit

SSH port

公网 IP/DNS

不得写密码。

---

# 二十二、最终输出连接状态表

最后回复用户时必须清晰给出：

| Service | Server status | Server bind | Local endpoint | Auth | Validation |
|---|---|---|---|---|---|
| MySQL | ... | 127.0.0.1:3306 | 127.0.0.1:13306 | enabled | ... |
| Redis | ... | 127.0.0.1:6379 | 127.0.0.1:16379 | enabled | ... |
| Milvus | ... | 127.0.0.1:19530 | 127.0.0.1:19530 | enabled | ... |
| Milvus WebUI | ... | 127.0.0.1:9091 | 127.0.0.1:19091 | tunnel | ... |

然后给出：

1. 实际服务器公网 IP/DNS
2. SSH 实际端口
3. SSH Tunnel 的准确启动命令
4. 本地 Python 连接 endpoint
5. 数据目录
6. 当前 RAM 占用
7. 当前磁盘占用
8. 当前磁盘剩余
9. 哪些服务已经真实验收
10. 哪些仅配置未从用户电脑测试
11. 用户还需要手工填写哪些秘密
12. 下一步应该执行哪个项目模块

严禁输出真正密码。

如果生成了 secrets 文件：

只告诉用户它的路径。

---

# 二十三、完成定义

P00 只有满足以下条件才能标记 DONE：

MySQL：
RUNNING + HEALTHY

Redis：
RUNNING + HEALTHY

Milvus：
RUNNING + HEALTHY

Milvus：
Dense / BM25 / Hybrid / Filter 已真实测试

三个服务：
restart 后数据/预期行为正确

端口：
没有直接暴露公网

SSH Tunnel：
配置已生成

连接参数：
connections.env.example 已生成

磁盘：
风险可控

内存：
至少完成稳定观察

所有版本：
已 lock

所有测试：
有真实输出证据

如果任一项未完成，
状态必须精确写：

FAILED
PARTIAL
PENDING_CLIENT_VALIDATION
NOT_ACCEPTED

不能为了结束任务写 DONE。