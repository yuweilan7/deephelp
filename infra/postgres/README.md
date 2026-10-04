# M25 隔离 PostgreSQL 运行入口

此目录提供协议试验的固定镜像与复验入口。应用正式装配仍由当前发布选择；迁移完成状态看PROJECT_STATE/M25 handoff，不能以本说明或容器healthy当成业务迁移通过。

`compose.yaml`仅增加一台隔离PG18.6，固定镜像digest，服务器loopback15432、独立数据目录与配置。默认数据库`deephelp_m25_protocol`；应用用户为单人学习环境的完整权限用户，不添加企业式角色/配额。应用事实库、Langfuse元数据库、官方checkpoint的配置/迁移责任仍分开；这个协议库不承担现行账本或审批。

复制compose到明确的服务器隔离根（本机受保护定位见`.local/DEPENDENCIES.md`）。创建`config/postgres-pass`，随机密码且文件0600、父目录0700，不提交Git；镜像从该文件初始化，不能以后更换文件就假装数据库密码已改。PG18镜像数据卷挂载`/var/lib/postgresql`。在该隔离根运行：

```bash
sudo docker compose -f compose.yaml up -d --wait
```

驱动由根`uv.lock`锁定`psycopg[binary]==3.3.6`及`psycopg-pool==3.3.3`。不修改旧应用安装环境；Linux试验用3.14.7临时环境，从根锁导出对应依赖及hash后安装。探针源码与锁定驱动分别确认，生产三包/正式装配另由后续迁移验收。

连接JSON必须显式含五项：`host`、整数`port`、`dbname`、`user`、`password`。放在受保护配置或`.local`，报告与配置使用不同文件。Windows通过既有严格host key/key建立单独SSH转发：本机127.0.0.1:15432→服务器127.0.0.1:15432；现有infra/personal隧道各自独立。通过root workspace执行：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.probes.postgres_probe --help
py -3.14 -m uv run --locked python -m deephelp_tools.probes.postgres_probe --live --config .local/m25/postgres.json --inventory-source . --output .local/m25/windows-new.json
```

Linux在根workspace环境使用同一`python -m deephelp_tools.probes.postgres_probe --live --config ABSOLUTE_CONFIG --output ABSOLUTE_NEW_REPORT`。`--inventory-source`只在需要新源快照时使用；源库采用read-only一致性事务，只读取schema/精确行数/外键，不导出行内容。可仅指定`--inventory-source`进行只读盘点，不访问PG。

PG探针只接受`deephelp_m25_*`隔离库，在其中创建随机`m25_protocol_*` schema。固定动作涵盖准确类型、signed JSON字节、回滚/冲突、8并发唯一接收/CAS、事实/outbox原子提交、Read Committed、效果query行锁、SKIP LOCKED、锁超时、服务器实际观察后取消，以及所有池连接/后端关闭。默认总120秒，可在合理范围显式调`--timeout`；连接/池等待5秒、语句10秒、行锁3秒，固定故障锁另设100ms，连接重试窗口5秒。模型调用/token为0，不走P00 full或历史模型探针。

输出必须为新路径；PASS要求所有动作及清理通过。成功只删除本次随机schema；失败保留其名称和结构诊断，关闭所有资源，并由实现者定位后复验失败/受影响行为。连接错误原文可能含secret/SQL值，公共输出只保留异常类别、sqlstate和当前检查。必要最终报告留`.local/m25`，临时安装/传输/脚本在核对后精确清理；连接配置、密码和PG数据不因被忽略而删除。

默认pytest在收集前同时阻止同步/异步psycopg连接。binary libpq在C层连接，不能仅靠原Python socket守卫；loopback也可能是SSH真实库转发，实库始终从上述显式探针启动，不能用pytest的`--live`绕过。

本入口不导入全业务数据、不切现网、不卸载MySQL、不建立新完整发布、不批准恢复历史写操作。全迁移需M25下一特性的适配器/导入/备份恢复证据，官方saver及旧状态恢复由M26完成。
