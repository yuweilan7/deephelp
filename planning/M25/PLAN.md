# M25 实施计划

判断：`DIRECT`。就绪：`READY`（后续全持久迁移特性；首项协议试验已验，证据见 [交接](../../handoffs/M25.md)）。旧路线已保存在bak/mysql-otel。规格与完整消费者/类型映射见 [M25](../../docs/MODULES/M25_POSTGRES_FACTS.md#持久消费者与类型映射)。本文件只保留未完成工作；启动或续做“完成M25”时直接完成下列剩余范围，不再因首项已交付而停止。

1. **这次做成什么：** 在隔离应用PG库完成所有事实仓储、源只读稳定快照导入与另库备份恢复。固定同输入得到相同事实/历史/原receipt，重复消息零新模型/工具。覆盖源码列出的26张应用表；P00运维表登记在源快照中，不作为新业务仓储。历史checkpoint字节原样留迁移历史schema，M26另转官方saver；PG装配拒绝配置审批执行/恢复。
2. **从哪里改：** 从同步main建`feature/m25-postgres-facts`；以首项`adapters/postgres.py`的池生命周期/Windows Runner为基线，逐仓储替换SQL与错误边界，不引入SQLAlchemy或另一套领域DTO。按规格盘点同步app/services/tools正式装配、维护/学习/评测/直接SQL、迁移包资源、连接模板与指标数据库标签；源MySQL仅留专门导出/历史读取，旧部署不切换。接收/终结、session→question→operation、outbox lease、审核租约与release channel使用已实测Read Committed+显式行锁/CAS，保留事务边界；Uvicorn及子进程loop单独适配。
3. **怎样证明：** 先离线迭代全部受影响仓储契约、归属/版本/幂等/金额/签名/租约/取消边界并完成跨包回归，再用隔离PG固定动作验证：8并发接收唯一与终结CAS、session绑定、outbox失败再投影/旧token拒绝、审批领取与effect/query锁（固定动作，不启用图）、并发active/previous/revision与run引用。源只读REPEATABLE READ快照导出逐表主键/完整原值/稳定水位/规范化hash，导入保留原ID/签名字节、重置identity sequence，记录中断续跑清单，重复导入不得覆盖冲突值；验证全部26表行数/hash/关系/版本/状态/outbox消费、active/previous/run/审批/UNKNOWN引用。备份实际pg_dump，恢复另一新PG库，逐表复核并从恢复配置查询金额/券/活动、受保护历史与原receipt；恢复不触发业务效果。无模型请求/解析/内容变化时模型0次；如果装配影响内容，按ROADMAP选必要代表路径并在live前公布样本与调用/token预算，不串历史全部探针。
4. **什么时候停：** 数据/关系/原签名字节差异、驱动事务/入口loop问题无法在本特性修复、源快照/续跑不稳定或真实依赖不可恢复就保留源/导出/诊断并停止合并。新运行字节/配置不能套用旧完整发布PASS；保存旧清单/历史原样，不激活未经完整门禁的新请求版本。M25不切现网、不卸载MySQL、不启用未通过M26的审批恢复、不采购资源；源所有现有事实/配置/工件保留。
5. **怎样交付：** 所有验收在特性分支；仅凭全持久导入/业务查询/幂等与实际备份恢复实证更新M25 handoff/STATE，按AGENTS中文提交/推送/合并main/六SHA同步，最终干净main。若未完成全迁移，M26依赖保持未满足；M25全部特性交付后停止，下一M由用户或已授权的顺序调度启动。
