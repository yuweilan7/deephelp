# M25 隔离 PostgreSQL 运行入口

此目录提供M25隔离应用库、只读源导出、可续跑导入及实际备份恢复入口。新源码所有关系仓储统一PostgreSQL；现行云端MySQL安装保持，M30另切换。完成证据见PROJECT_STATE/M25 handoff。

`compose.yaml`仅增加一台隔离PG18.6，固定镜像digest，服务器loopback15432、独立数据目录与配置。默认数据库`deephelp_m25_protocol`；应用用户为单人学习环境的完整权限用户，不添加企业式角色/配额。应用事实库、Langfuse元数据库、官方checkpoint的配置/迁移责任仍分开；这个协议库不承担现行账本或审批。

复制compose到明确的服务器隔离根（本机受保护定位见`.local/DEPENDENCIES.md`）。创建`config/postgres-pass`，随机密码且文件0600、父目录0700，不提交Git；镜像从该文件初始化，不能以后更换文件就假装数据库密码已改。PG18镜像数据卷挂载`/var/lib/postgresql`。在该隔离根运行：

```bash
sudo docker compose -f compose.yaml up -d --wait
```

驱动由根`uv.lock`锁定`psycopg[binary]==3.3.6`及`psycopg-pool==3.3.3`。不修改旧应用安装环境；Linux试验用3.14.7临时环境，从根锁导出对应依赖及hash后安装。探针源码与锁定驱动分别确认，三包的仓储/装配与包资源由全持久特性验收。

连接JSON必须显式含五项：`host`、整数`port`、`dbname`、`user`、`password`。放在受保护配置或`.local`，报告与配置使用不同文件。Windows通过既有严格host key/key建立单独SSH转发：本机127.0.0.1:15432→服务器127.0.0.1:15432；现有infra/personal隧道各自独立。通过root workspace执行：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.probes.postgres_probe --help
py -3.14 -m uv run --locked python -m deephelp_tools.probes.postgres_probe --live --config .local/m25/postgres.json --inventory-source . --output .local/m25/windows-new.json
```

Linux在根workspace环境使用同一`python -m deephelp_tools.probes.postgres_probe --live --config ABSOLUTE_CONFIG --output ABSOLUTE_NEW_REPORT`。`--inventory-source`只在需要新源快照时使用；源库采用read-only一致性事务，只读取schema/精确行数/外键，不导出行内容。可仅指定`--inventory-source`进行只读盘点，不访问PG。

PG探针只接受`deephelp_m25_*`隔离库，在其中创建随机`m25_protocol_*` schema。固定动作涵盖准确类型、signed JSON字节、回滚/冲突、8并发唯一接收/CAS、事实/outbox原子提交、Read Committed、效果query行锁、SKIP LOCKED、锁超时、服务器实际观察后取消，以及所有池连接/后端关闭。默认总120秒，可在合理范围显式调`--timeout`；连接/池等待5秒、语句10秒、行锁3秒，固定故障锁另设100ms，连接重试窗口5秒。模型调用/token为0，不走P00 full或历史模型探针。

输出必须为新路径；PASS要求所有动作及清理通过。成功只删除本次随机schema；失败保留其名称和结构诊断，关闭所有资源，并由实现者定位后复验失败/受影响行为。连接错误原文可能含secret/SQL值，公共输出只保留异常类别、sqlstate和当前检查。必要最终报告留`.local/m25`，临时安装/传输/脚本在核对后精确清理；连接配置、密码和PG数据不因被忽略而删除。

默认pytest在收集前同时阻止同步/异步psycopg连接。binary libpq在C层连接，不能仅靠原Python socket守卫；loopback也可能是SSH真实库转发，实库始终从上述显式探针启动，不能用pytest的`--live`绕过。

协议命令只验证随机schema。下方迁移命令覆盖所有应用事实；不切现网、不卸载MySQL、不建立新完整发布、不批准恢复历史写操作。官方saver及旧状态恢复由M26完成。

## 应用连接与Windows转发

复制`postgres.example.json`到`.local`或受保护配置，填写已有PG密码；五字段严格校验，不接受隐式MySQL/demo回退。启动新源码前设置：

```powershell
& infra/postgres/tunnel.ps1 -Action start
& infra/postgres/tunnel.ps1 -Action health
$env:DEEPHELP_POSTGRES_CONFIG = (Resolve-Path .local/m25/facts.json).Path
```

health只证明15432转发可连接，数据库语义由显式验收证明。停止使用`-Action stop`；该入口只关闭自己记录并核对的SSH进程，旧infra/personal隧道独立。正常应用/Uvicorn/工具CLI保留原入口循环；Windows池拥有单独Selector线程，支持Proactor中的MCP子进程，取消等待数据库动作结束后再归还连接，关闭池后退出线程。协议探针的Runner只用于直接驱动资格试验。

应用短写使用Read Committed+锁/CAS；记忆多表窗口和Business事实+证据使用局部Repeatable Read。旧checkpoint字节保存在`m25_legacy`，PG装配在M26前拒绝审批图创建/执行/恢复，学习GET可只读旧历史。原MySQL只在tools只读导出中出现；M30旧安装/脚本保留，不用旧完整release对新包宣称通过。

## 稳定快照、导入与复验

须安装root workspace的tools/services。源用已有本机MySQL配置，目标必须是显式`deephelp_m25_*`隔离库；所有报告使用新路径。导出以read-only Repeatable Read捕获每表原值、主键、完整列类型、外键及每表行数/规范化hash，包含P00运维行但导入仅应用表。文件有完成尾记录和校验，未完成导出不可导入，失败文件保留诊断后换新路径。server_uuid/GTID只是快照开始时观测，稳定内容水位以同事务主键/原值和逐表hash为准，不声称与某一binlog点原子绑定。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.postgres_migration export --live --source-root . --snapshot .local/m25/source-new.jsonl.gz --output .local/m25/export-new.json
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.postgres_migration import --live --snapshot .local/m25/source-new.jsonl.gz --target-config .local/m25/facts.json --output .local/m25/import-new.json --timeout 900
py -3.14 -m uv run --locked python -m deephelp_tools.evaluation.postgres_migration verify --live --snapshot .local/m25/source-new.jsonl.gz --target-config .local/m25/facts.json --output .local/m25/verify-new.json
```

导入前完整核验文件，再按复合外键顺序及500行批次导入。每批数据与`m25_legacy.migration_receipts`同事务提交，源快照SHA绑定每表进度；中断后使用**同一文件/目标、另一个新报告路径**续跑，不重新导出源覆盖进度。`--stop-after-batches 1`用于确定性中断演练，报告INTERRUPTED且退出2；其余成功退出0、失败退出1。重复导入不覆盖已有值，最终全表原值/hash差异必须FAIL，不能把ON CONFLICT吞掉后称成功。全部完成后重置identity sequence。完整校验使用服务端游标分批读取，保留原连接超时而不一次缓存慢链路的大结果。

源JSON文本保存为PG json而非jsonb，bytea保存typed bytes；规范化校验分别保留文本/二进制原值、Decimal、UTC微秒、NULL/bool、文本标识及前导零。关系回查覆盖message→run→question、成员归属、active/previous/run release引用和operation绑定；状态/版本/outbox/UNKNOWN原值已包含逐表hash。若源出现新表或整数超bigint，先明确映射，不跳过/截断。

## 真实备份与另库恢复

在PG主机用有Docker权限的账号运行，`DUMP`为新的绝对路径，父目录已存在且受保护：

```bash
sudo bash infra/postgres/backup.sh deephelp_m25_facts /protected/path/facts.dump
sudo bash infra/postgres/restore.sh deephelp_m25_restored /protected/path/facts.dump
```

备份实际`pg_dump --format=custom`并检查可读目录、保存SHA256；失败保留.partial。恢复核对实际dump的hash，CREATE必须是新库，禁止覆盖现行库，`pg_restore --single-transaction --exit-on-error`原子恢复。恢复后用新的恢复库JSON运行`postgres_migration verify`全表校验，再用`postgres_migration read`核对原receipt零调用重放、受保护历史、金额记录及历史checkpoint；该动作不执行图或触发旧效果。恢复成功以这些结果为准。

仓储固定动作与独立Business MCP重启入口：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.probes.postgres_facts --live --config .local/m25/facts.json --snapshot .local/m25/source-new.jsonl.gz --output .local/m25/facts-new.json --timeout 900
py -3.14 -m uv run --locked python -m deephelp_tools.probes.postgres_business_http --live --config .local/m25/restored.json --snapshot .local/m25/source-new.jsonl.gz --workdir .local/tmp/m25-task --output .local/m25/http-new.json --timeout 600
```

workdir需先创建。固定动作使用真实PG仓储/协议、独立HTTP进程和受控合成权益；模型调用/token均0，不证明模型质量或企业业务泛化。发布固定动作只验私有数据库提交阶段，公共完整门禁仍拒绝旧版本；测试指针/记录精确清理后再核对全源hash。失败报告/快照保留，必要故障和恢复细节记录交接；最终快照、dump及受保护配置长期保留，临时安装/传输/脚本核对后删除。
