# M10 实施计划

判断：PROBE_FIRST → DIRECT。基线 `cf44a92cba136d07131035383d6e2afc6f396aae`。学习环境权限修正已先验收、合并；原M10改动经备份核对后续接。

1. **这次做成什么**：一个“可恢复的多问题记忆”特性。无 hint 的消息新建问题；显式 hint 只续接同主体/session 的开放问题，版本检查防并发覆盖。MySQL 保存原文、实体来源、更正、状态及 outbox；Redis 有界窗口/关键词/活跃问题缓存和 Milvus 事件摘要均可重建。关闭后普通消息新建，显式重开单独审计。自动交错归属、审批执行、写工具、硬中断 run 重领留后续模块。
2. **从哪里改**：增量扩展 M08 MessageLedger、同一 Conversation 和 DTO；新增 CaseRepository/MemoryPort、010 迁移、投影 worker、真实验收入口。保留根 workspace/Python/单锁/infra；只新增既有 P00 使用的 redis 客户端依赖。不修改 M05/M09 集合。
3. **怎样证明**：先 health、Redis 版本键读写/TTL与实际 Embedding 内容探针；用合成身份/session、M10 表/缓存前缀/独立向量集合验多问题、重复/乱序、更正、CAS、过期命中回查、投影失败重试/乱序、专用缓存删除后恢复、新进程回读及长历史裁剪。应用账号已放开权限；Redis按session generation写带TTL的事务投影，Milvus按question/version保存版本行，worker远程I/O不持有业务事务。默认回归无外部网络。执行 ROADMAP 五项检查；特性分支完整真实验收，main 最小复验并保留累计预算。
4. **什么时候停**：所有外部操作带总时限、子时限、有限重试和累计调用/token/费用上限，原始配置/诊断留 .local/m10。确认实际依赖无法恢复即停，不以 Mock 替代、不合并。只清理本次专用合成缓存/集合，不执行 FLUSHALL/FLUSHDB/P00 full。
5. **怎样交付**：`feature/m10-memory-lifecycle`；验收后中文提交、推送、合并、复验、双分支同步并回到干净 main。更新 CONTRACTS、运行入口、M10 规格/handoff 与 PROJECT_STATE。完成后不启动 M11。
