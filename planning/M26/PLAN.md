# M26 实施计划

判断：`PROBE_FIRST`。就绪：`BLOCKED`（M25正式持久适配与隔离恢复未交付）。规格见 [M26](../../docs/MODULES/M26_OFFICIAL_CHECKPOINTS.md)。

1. **这次做成什么：** 完成M26全部特性：先用锁定官方AsyncPostgresSaver证明pause/resume/parent/pending writes与隔离旧状态转换；首项验收交付后依据结果更新本PLAN，继续正式装配与完整历史恢复特性。13段引擎、批准权和operation事实不重写。
2. **从哪里改：** M15/M18/M21/M23及M25 handoff；approval.py、checkpoints.py、approval_store及发布运行守卫/合成权益服务。根锁增官方postgres保存器，薄连接装配；旧reader只在迁移工具。`feature/m26-postgres-saver-proof`从同步main建立。
3. **怎样证明：** 模型0次，固定合成动作在独立进程pause→退出→读回→resume/重复resume，另验UNKNOWN/query/ABSENT锁与效果一次、越权零效果。实际读parent/pending writes，导入重跑幂等。选一份含必要状态的隔离MySQL快照，只读旧源；核对原版本/runtime-v2/资产恢复资格，不能仅验证新空线程。
4. **什么时候停：** 锁定包/序列化不兼容、公共API不能保留必要状态、身份/资产绑定变化或效果次数不正确时停止；保存对比与恢复对象，不清线程或重发未知效果换通过。无现网切换/卸载。
5. **怎样交付：** M26 handoff逐状态区分通过/未验/不能转换，STATE仅记实际范围；各特性分别合入main并按AGENTS同步，模型0。首试验后在同一会话继续正式装配与完整历史恢复，无需用户另启；M26全部特性交付后停止。
