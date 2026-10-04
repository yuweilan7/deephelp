# M26 实施计划

判断：`DIRECT`。就绪：`READY`（官方保存器/旧状态首试验已交付，见[交接](../../handoffs/M26.md)）。规格见 [M26](../../docs/MODULES/M26_OFFICIAL_CHECKPOINTS.md)。本文件只保留正式装配与完整历史恢复的未完成事项。

1. **这次做成什么：** 正式新PG审批只装配已验证官方saver；原整数线程继续锁定公共整数版本序列，新线程用官方版本序列。保留原图、operation事实与全部原绑定，接通学习查询和历史恢复，完成M26全部剩余范围。
2. **从哪里改：** 从同步main建`feature/m26-official-approval-runtime`。ApprovalService显式注入官方saver并随assembly关闭；migrate入口执行独立官方setup。app不再含旧自建codec，迁移工具保留只读reader，学习查询接正式saver。沿用M15/M18/M21/M23/M25及首试验handoff。
3. **怎样证明：** 模型0次，固定合成动作独立进程pause/审批/退出/resume/重放；效果前后、ledger先于checkpoint、丢响应/下游中断、ABSENT、取消、并发领取和query/效果行锁互斥；效果保持一次，闭合/越权/绑定失效零效果。先离线边界，再独立PG恢复库读取/恢复M25历史，逐状态报告原计划/资产/审批绑定与正确效果次数。原完整release仍走版本门禁，必要旧运行/适配恢复明确验证，不改旧manifest或清UNKNOWN。
4. **什么时候停：** 锁定包/序列化不兼容、公共API不能保留必要状态、身份/资产绑定变化或效果次数不正确时停止；保存对比与恢复对象，不清线程或重发未知效果换通过。无现网切换/卸载。
5. **怎样交付：** M26 handoff逐状态区分通过/未验/不能恢复，STATE仅记实际范围；所有验收在特性分支，合入main并按AGENTS同步；完成后只留必要最终证据与恢复资产，M26全部交付后停止，不启动M27。
