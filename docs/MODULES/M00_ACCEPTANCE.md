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
| D07 | 设计兼容 | 两份独立请求/响应草案 | ID/状态/错误/next_action语义一致 | M00/S06，不等于业务集成 |
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

M00允许通过文档走查确定契约，不要求现在启动业务中间件；但不能把本矩阵未来行改成PASS来凑退出条件。没有独立复核证据时状态保持DESIGN_READY/REVIEW_PENDING。
