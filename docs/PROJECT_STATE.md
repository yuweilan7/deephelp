# 项目当前状态

更新：2026-10-02。只记录实现与实际验证；命令见 [ROADMAP](ROADMAP.md) / [LOCAL_SETUP](LOCAL_SETUP.md)。

| 范围 | 状态与证据 | 限制 |
|---|---|---|
| 工程 | Python3.14.7；根uv workspace、单一uv.lock；modules/deephelp-app | uv须满足根pyproject要求，本机启动器见LOCAL_SETUP |
| P00 | MySQL SELECT、Redis鉴权PING、Milvus元数据读取均PASS；报告 `.local/infra-health/client-health.json` | 只读连接验证，不是容量/业务检索/full复验；[历史交接](../handoffs/P00.md) |
| M00 | DESIGN_ACCEPTED；[交接](../handoffs/M00.md) | 设计基线，不是已实现业务系统 |
| M01 | IMPLEMENTED_OFFLINE_VERIFIED；[交接](../handoffs/M01.md) | health为骨架；converse为501/NOT_IMPLEMENTED；live网关未实现 |
| M02–M14、M16–M18 | NOT_IMPLEMENTED | 规格存在；M02尚无实施计划/迁移/handoff |
| M15、M19 | HARDENING_NOT_IMPLEMENTED | 可后移，业务写工具前必须M15 |
| M20、M21 | OPTIONAL_NOT_IMPLEMENTED | 不阻塞客诉核心路线 |
| 模型/业务联调 | NOT_RUN | Key读取目录HTTP200、账户额度有快照；chat/schema/tool/embed及Key抵扣归属未实测 |

M01已有客户端生命周期、deadline/预算/取消、最小DTO、错误/trace、fake Ports和三个异步实验；未安装LangGraph、未连接真实业务库或调用模型。M02原位扩展类型，M03实现真实网关。

最近M01离线验证：Ruff检查/格式、mypy退出0；Python3.14.7下 **48 passed、1 skipped**，253条既有pytest-asyncio弃用警告。integration使用合成loopback HTTP，e2e验证组装/退出，不是业务或云端e2e。CI配置存在，本机结果不冒充远端CI执行证据。

LOCAL_SETUP指向本机凭据/模型摘要/连接结果；调用前实时核对资格。自动换模型未实现，live预算未设置，不能因Key存在自动调用。

原始P00报告、旧安装提示、一次性生成工具和上游参考副本已移出当前工作树，保存在 `.local/archive/context-cleanup-20261002/` 和Git历史。运行配置、运维/验收工具、原PDF、M01源码/测试和模块规格保留；本次未启动下一M。

## 后续真实门禁

- M03：实时资格与调用/token/费用硬上限；分别验chat/schema/tool/embed及embedding signature。
- M05：专用集合真实导入/检索/容量/索引；P00四维向量不证明语义效果。
- M15：MySQL持久saver兼容、审批并发、下游幂等/查询与崩溃恢复。

MySQL保存业务事实，Redis/Milvus是可重建投影，checkpoint不是账本。门禁按当前特性启用，不阻塞纯离线工作。
