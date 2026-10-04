# M26｜官方 AsyncPostgresSaver 与历史审批恢复

**状态：** PLANNED；依赖 M25，首特性为官方保存器和旧状态转换的最小真实试验。

**前置：** M25 handoff，M15/M18/M21/M23 的审批、版本引用与恢复证据；核对旧备份分支bak/mysql-otel。见 [计划](../../planning/M26/PLAN.md)。

## 采用方式

新路线使用官方 `langgraph-checkpoint-postgres` 的 `AsyncPostgresSaver`；保留现有 approval_wait→execute_operation→reconcile_effect、interrupt/Command(resume) 与唯一业务仓储。13 段主链仍复用既有引擎，不借迁库重建为另一套 LangGraph 流程。

核对根锁中的 LangGraph/checkpoint 与候选 postgres 包、psycopg 的兼容性及 Python3.14.7 双平台。官方 setup/migration 与连接参数按锁定实现核实；采用独立 schema/搜索路径或经证实的表隔离方式，避免与业务迁移混管。只做连接生命周期/观测的薄接缝，不复制官方内部 SQL 重新造一个 PostgreSQLSaver。

checkpoint 记录执行状态，审批、operation、效果及授权仍由 PostgreSQL 业务账本决定。saver 与业务仓储不是一个自动原子事务；保留“业务提交成功而 checkpoint 落后”时先读账本推进、UNKNOWN/IN_FLIGHT 先 query 的恢复机制。行锁不能跨人工等待、模型或网络调用。

## 旧 checkpoint 转换

先用旧锁定序列化器和 MySQLSaver 的只读接口解析隔离快照，再试验官方 aput/aput_writes 等公共能力能否保留 thread、namespace、checkpoint_id、parent、channel_versions、versions_seen、metadata、pending writes 的任务/索引语义。不得猜表结构直接 INSERT，也不能把两种 saver 的二进制 blob 当成同格式复制。

保留原 run/operation/参数/SOP/主体/资产绑定。转换报告逐线程说明源摘要、目标引用、完整性与重启后读取结果；无法保留 ID 时必须有受校验、幂等且可追溯的映射，业务引用不重编号。签名绑定对象不得为迁库而重签成新计划。

从真实隔离旧状态验证：待审批、批准未发出、已发出但结果未知、业务完成而 saver 落后，以及带 parent/pending writes 的恢复。不能通过清空旧状态、直接关闭 UNKNOWN、重走模型计划或无条件创建新线程消除迁移难点。若官方公共接口无法等价转换，停止该特性并记录具体差异；是否采用账本重建必须另按原契约逐状态证明，不能静默丢弃游标/待写。

旧审批继续执行还受 M18 原运行/工件绑定限制；数据库状态转换通过不等于允许新运行包冒充旧版本。首试验必须核验现行 `runtime-v2` 守卫是否允许原版本恢复；不允许时，明确保存可读取审计状态，并验证保留旧运行/适配恢复的办法，再解除迁移门禁。不得把这种限制模糊成“历史恢复通过”。

## 验收与停止

使用独立进程实际 pause→退出→读取→resume、重复 resume，以及取消、并发领取、在效果前后中断、查询 ABSENT 与效果提交互斥、导入重跑。原业务效果计数保持一次；拒绝/越权/绑定失效为零次；模型 0 次，以固定合成动作验证数据库与恢复协议。

读回官方 checkpoint 的 parent/pending writes，核对授权/参数/版本不变，故障路径有必要恢复证据。通过后新路线正式装配只使用官方 saver；旧自建 saver 完整保留在bak/mysql-otel。必要旧状态 reader 可留迁移工具包，不进入正式运行。现网切换留 M30；完成后停止。

官方依据：[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。PostgreSQL 是官方提供的生产后端之一，LangGraph 并非只支持 PostgreSQL。
