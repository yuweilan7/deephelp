# M00 验收矩阵

本文件是验收规格；执行证据写reports，不在这里把所有行默认为PASS。设计检查与未来运行测试必须分列。

| ID | 层级 | 输入 / 故障 | 期望 | 执行位置 |
|---|---|---|---|---|
| D01 | 源图人工核对 | p19概述与p57详细图 | 记录12/13差异并采用13段，不杜撰一致 | M00/S01 |
| D02 | 设计/静态 | manifest包含循环、未知前置、丢模块 | validator失败，不能推进 | M00/S05 |
| D03 | 设计/静态 | PDF哈希/页数/本地路径改变 | 身份或约定不匹配失败；不复制错误文件 | M00/S05 |
| D04 | 设计走查 | 订单A明确改成B | 同question新实体版本；旧审批失效 | M00/S04；运行版M04/M10/M15 |
| D05 | 设计走查 | qA→qB→补qA | 两问题不合并，m3回qA或澄清 | M00/S04；运行版M11 |
| D06 | 设计走查 | 工具成功+崩溃+重复resume | 效果至多一次；UNKNOWN先对账 | M00/S04；运行版M15 |
| D07 | 设计兼容 | 两份接口视角请求/响应草案 | ID/状态/错误/next_action语义一致；允许单会话双视角 | M00/S06，不等于业务集成 |
| D08 | 文档工具unit | 非法module、路径逃逸、错误源hash | fail closed，不读取任意文件/覆盖资料 | M00/S05 |
| U01 | 业务unit（未来） | 清洗截断、前导零、否定、更正 | 事实保真/来源完整 | M02/M04，当前NOT_RUN |
| U02 | 业务unit（未来） | 最终code非top1无有效override | 拒收决策；不返回假正常 | M02/M12，当前NOT_RUN |
| U03 | 业务unit（未来） | 幂等键冲突/缺槽位/跨租户 | 错误分类固定、禁止工具/错误case写入 | M02/M08，当前NOT_RUN |
| I01 | MySQL/Redis integration | 提交成功缓存失败、清空缓存 | 事实不丢，outbox重建 | M10，当前NOT_RUN |
| I02 | Milvus integration | stale ACTIVE/同维异模型 | MySQL回查过滤/signature拒绝 | M05/M10，当前NOT_RUN |
| I03 | 真实MCP协议 | list_tools/call_tool超时/非法参数 | 协议/白名单/错误归一化；不是函数Mock | M06，当前NOT_RUN |
| L01 | 真实模型live | chat/schema/tool/embed各一次受限探针 | 独立能力证据、成本/失败记录 | M03，当前NOT_RUN |
| E01 | 真实e2e | 真实模型+Milvus+MCP Mock三类场景 | 完整trace和工具ledger；注明合成业务 | M08，当前NOT_RUN |
| F01 | 真实进程故障 | checkpoint落后账本/审批并发 | 恢复不越权不重放效果 | M15，当前NOT_RUN |

## S06独立视角复核方法

视角A仅用CONTRACTS编写“缺订单”请求/响应草案；视角B仅用ARCHITECTURE及M00场景写相同流程的状态/工具次数预期。对比Question=WAITING_SLOT、outcome=CLARIFY、run正常结束、tool_calls=0、下一消息不是resume。再对跨用户拒绝和重复审批做相同对照。必要时可用两个独立会话，必须记录实际输入/输出，不声称本轮已真实运行两个会话。

M00允许通过文档走查确定契约，不要求现在启动业务中间件；但不能把本矩阵未来行改成PASS来凑退出条件。没有双视角复核证据时状态保持DESIGN_READY/REVIEW_PENDING；单会话双视角不称为独立会话审查。

## S04逐事件设计fixtures（S06补齐）

以下全部是合成输入和预期，不是执行日志。Qv为Question.version，Ev为实体版本；工具列为本步允许的“业务只读/写入/对账查询”调用次数，效果列为本场景累计外部业务写效果。MySQL记账不是外部工具效果。`—`表示该事件不产生新消息或响应，不是新枚举。瞬态步骤可无ResponseEnvelope。每个场景独立，身份均为tenant-demo/user-a；r、q只是合成ID，绝不使用订单号当question_id。

### D04：订单更正，同时废止未发送计划

会话session-correction。此分支假设M15之后且订单A的获准合成SOP已有齐全槽位；更正为B后缺B的promotion_id。M15之前到计划写操作时必须拒绝，不能执行本分支。H-A为A计划参数的符号hash，不是真实摘要值。

| 事件 / 证据 | message / request / run / question | 实体与版本 | Question / Run / Operation / Approval | outcome / next_action | 工具 / 效果 | 禁止行为 |
|---|---|---|---|---|---|---|
| A1 新诉求；证据m1 | m1 / req-a1 / r1 / q1 | order=DEMO-A，Ev1；Qv1 | ACTIVE / RUNNING / — / — | — | 0/0/0；0 | 用order_id替代q1 |
| A2 计划就绪；证据m1、plan-A | — / — / r1 / q1 | Ev1；Qv1→2；审批绑定Qv2、H-A、SOP v1 | WAITING_APPROVAL / WAITING_APPROVAL / op-A:PREPARED / ap-A:PENDING | PENDING_APPROVAL / await_approval | 0/0/0；0 | 批准前发写工具 |
| A3 “说错了，是B”；更正事件引用m1/m2 | m2 / req-a2 / r2 / q1 | order=DEMO-B，Ev1→2；Qv2→3；保留A旧值/来源 | ACTIVE / r1:CANCELLED、r2:RUNNING / op-A:CANCELLED / ap-A:REVOKED | — | 0/0/0；0 | 静默union A/B；旧hash套新参数 |
| A4 B槽位校验/收口；证据m2 | — / — / r2 / q1 | Ev2；Qv3→4；promotion_id缺失 | WAITING_SLOT / SUCCEEDED / op-A:CANCELLED / ap-A:REVOKED | CLARIFY / provide_slots | 0/0/0；0 | 新建q掩盖更正；interrupt补槽 |
| A5 迟到的旧批准；证据ap-A撤销记录 | — / req-a3 / r1引用 / q1 | Ev2、Qv4不变 | WAITING_SLOT / r1:CANCELLED / CANCELLED / REVOKED | REJECTED + APPROVAL_INVALID / refresh_approval | 0/0/0；0 | 复活r1；重新发送op-A |

A3与执行领取必须CAS协调：本fixture选择“更正先赢、尚未发送”；若领取先赢，op-A保留IN_FLIGHT/UNKNOWN和原参数，进入D06对账路径，不能宣称已撤销或效果0。

### D05：券问题→活动问题→补券问题

会话session-interleaved；只读工具返回的是获准合成fixture事实。每步是一轮收口快照，首次持久化可直接记录最终业务状态为Qv1；本轮内部暂态不逐次落库。

| 事件 / 证据 | message / request / run / question | 归属、实体、版本 | Question / Run | outcome / next_action | 工具 / 效果 | 禁止行为 |
|---|---|---|---|---|---|---|
| B1 “券不能用”；m1 | m1 / req-b1 / r1 / qA | members=[m1]；order/coupon未知，Ev0、Qv1 | WAITING_SLOT / SUCCEEDED | CLARIFY / provide_slots | 0/0/0；0 | 从候选排除WAITING_SLOT |
| B2 “另外，B参加什么活动”；m2、tool-B | m2 / req-b2 / r2 / qB | qB members=[m2]；order=DEMO-B，Ev1、Qv1；qA保持Qv1 | qB:RESOLVED、qA:WAITING_SLOT / SUCCEEDED | ANSWERED / none | 1/0/0；0 | 把qA并入qB |
| B3 “刚才券COUPON-001，订单A”；m3、tool-A | m3 / req-b3 / r3 / qA | qA members=[m1,m3]；order=DEMO-A、coupon=COUPON-001，Ev0→1、Qv1→2；qB保持Qv1 | qA:RESOLVED、qB:RESOLVED / SUCCEEDED | ANSWERED / none | 1/0/0；0 | 只归最近qB；相似边跨明确订单 |

每个已归属业务轮600主分类服务调用至多1次；工具及内部模型尝试另计。B3若归属证据不足，则替代预期为question_id=null、CLARIFY/choose_question、r3=SUCCEEDED、工具0，两个旧question版本均不变；不能先写入qB再撤销来掩盖错误归属。

### D06：批准→写效果成功→崩溃→对账→重复批准

会话session-approval，固定qC/rC、op-demo-1、ap-C、参数hash H、SOP v1。M15之后的合成场景；下游必须支持按op-demo-1查询事实。审批版本Av独立于Question版本。

| 事件 / 证据 | message / request / run / question | 版本和绑定 | Question / Run / Operation / Approval | outcome / next_action | 工具 / 效果 | 禁止行为 |
|---|---|---|---|---|---|---|
| C1 计划等待；mC、plan-C | mC / req-c1 / rC / qC | Ev1、Qv3；ap-C绑定Qv3、H、v1、主体/期限；Av1 | WAITING_APPROVAL / WAITING_APPROVAL / PREPARED / PENDING | PENDING_APPROVAL / await_approval | 0/0/0；0 | 新消息即批准 |
| C2 独立鉴权入口批准；decision-C | — / req-c2 / rC / qC | Ev1、Qv3不变；Av1→2，CAS唯一成功 | WAITING_APPROVAL / WAITING_APPROVAL / PREPARED / APPROVED | —（领取前内部步骤） | 0/0/0；0 | 两个批准者各发工具 |
| C3 唯一领取并发出，成功后未记账即crash；claim-C、下游记录 | — / — / rC / qC | 核验Qv3；领取提交Qv3→4、Ev1，固定H/v1；Av2 | ACTIVE / RUNNING / IN_FLIGHT / APPROVED | —（响应丢失） | 0/1/0；1 | 跨网络持有MySQL长事务 |
| C4 恢复协调器确认旧执行者失效；claim-C | — / req-c4 / rC / qC | Ev1、Qv4、Av2不变 | ACTIVE / RUNNING / UNKNOWN / APPROVED | —（恢复内部步骤） | 0/0/0；1 | 把UNKNOWN当失败自动重写 |
| C5 按operation查询已成功并补ledger/outbox；query-C、下游结果 | — / req-c4 / rC / qC | Ev1、Qv4→5、Av2不变 | RESOLVED / SUCCEEDED / SUCCEEDED / APPROVED | ANSWERED / none | 0/0/1；1 | checkpoint覆盖ledger；再发权益 |
| C6 同一批准重投；decision-C、ledger引用 | — / req-c6 / rC / qC | Ev1、Qv5、Av2不变；读取既有完成结果 | RESOLVED / SUCCEEDED / SUCCEEDED / APPROVED | ANSWERED / none | 0/0/0；1 | 新run/op；再次PENDING→APPROVED |

若C5查询仍未知，本轮返回HANDOFF + OPERATION_UNKNOWN / reconcile_operation，保留rC/op-demo-1可对账引用、禁止新写调用；Question可转HANDED_OFF、Run可记FAILED。后续对账恢复必须重新授权并沿用原操作，不解释为新消息resume。恢复状态机和超时领取协议由M15运行验证；本轮仅冻结安全预期。对账网络查询可多次，累计效果仍为1；预算耗尽停止本轮查询。

### 其余反例及后续运行门禁

| 输入 | 设计预期 / 效果约束 | 运行验证责任 |
|---|---|---|
| 同订单两个不同诉求 | 两个question；不按订单唯一键合并 | M02/M11 |
| 他人case或同字符串订单跨用户 | FORBIDDEN；不暴露存在性；业务写0/工具0 | M02/M08 |
| 歧义“就是那个” | question=null、CLARIFY/choose_question、旧Qv不变、工具0 | M11 |
| 与RESOLVED旧问题相似的新诉求 | 默认新question；旧问题只可受控参考，不隐式重开 | M10/M11 |
| WAITING_APPROVAL时普通“好的” | 普通新message/run；可CLARIFY；原审批仍PENDING、原run仍WAITING_APPROVAL、写工具0 | M02/M15 |
| 发布SOP v2，原run固定v1 | 原计划仍引用v1；若显式迁移计划到v2，旧批准失效并重审 | M14/M15 |
| 审批过期/主体无权/参数变化 | APPROVAL_EXPIRED/FORBIDDEN/APPROVAL_INVALID；未领取时工具0 | M15 |
| 两批准者同时提交同决定 | 一次CAS迁移，另一请求读已有决定；最多一个执行领取 | M15 |
| 批准与拒绝并发 | 一个决定生效，另一VERSION_CONFLICT/APPROVAL_INVALID；不覆盖获胜决定 | M15 |
| 缓存更新失败/向量仍ACTIVE | MySQL事实可返回、outbox修复；回查已RESOLVED就不合并 | M10/M11 |
| 非top1且无合法override | 不接受决策；有限修复后仍失败则MODEL_OUTPUT_INVALID，不发SOP工具 | M02/M12 |
