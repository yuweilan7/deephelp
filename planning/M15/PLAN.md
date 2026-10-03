# M15 实施计划

> 现行验收以[AGENTS](../../AGENTS.md#按改动范围验收)和[ROADMAP](../../docs/ROADMAP.md#按影响选择验收)为准。下文旧计划/记录中的全套live、四能力、四组或main复验不是当前默认门禁；历史证据保留。

判断：PROBE_FIRST。基线为同步后的 main，特性分支 `feature/m15-durable-approval`。

1. **这次做成什么**：现有 M14 计划在同一 converse 主链进入持久待审批；专用端点明确批准/拒绝/撤销并定位原 run。合成权益操作在应用/下游重启与未知结果后对账，业务效果不重复。不接企业支付/退款。
2. **从哪里改**：先锁定 LangGraph 并以既有 aiomysql 实现独立 MySQL saver，真实 interrupt/restart/resume 通过后再实施。复用唯一 DTO、MySQLCaseRepository、SOPPlan、M16 事实/调试接口；增量 M15 审批/操作/checkpoint/合成效果表。保留原 13 段、唯一 600、Python/workspace/单锁/infra。
3. **怎样证明**：先真实 saver 兼容及不同进程恢复；再审批身份/版本/参数/期限/并发领取、普通消息不批准、拒绝/撤销/过期、UNKNOWN 查询优先、工具成功后丢响应/落账前崩溃、checkpoint 裂缝、缓存丢失/投影滞后。区分网络调用与持久业务效果。特性与 main 均跑 ROADMAP 全量检查和必要真实 HTTP/进程故障矩阵。报告留 `.local/m15`。
4. **什么时候停**：最小兼容门禁未通过不宣称持久能力、不合并；真实依赖不可恢复按 AGENTS 停止。模型/远程调用用现有共享预算及有限 deadline，不循环重放 UNKNOWN，不持行锁跨外部请求。
5. **怎样交付**：完成验收、审查差异、中文提交、push 特性、合并/复验/push main、特性 fast-forward、六 SHA 核对、干净 main。更新 M15 handoff/PROJECT_STATE/契约/运行入口，并只读核对 M02–M18 依赖 M15 的遗漏；不启动其他 M 的新特性。
