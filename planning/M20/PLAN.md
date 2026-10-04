# M20未完计划

第1特性Business服务已交付，接口与证据见[handoff](../../handoffs/M20.md)，不重复实施。第2特性尚未启动；用户启动后另建`feature/m20-retrieval-service`，仍由一个主写者实施。

1. 复用锁定SDK 2.2及本次HTTP、委托、取消接缝，先做实际Retrieval MCP协议探针，再接原Dense/HybridPort远端适配器。固定scope、版本与归属，不让请求指定任意集合、连接或文件。
2. 服务端复用既有Embedding、Milvus与BM25/Hybrid排序，不搬事务、outbox或checkpoint。有限预算预留、实际用量、未知尝试及receipt在应用侧累计，不跨网络传递ExecutionBudget，不自动无限重试或补预算。
3. 离线覆盖schema、scope、归属、取消、断连、重启和共享预算；固定查询核对候选ID、分数类型、排序、签名与版本。live前说明本轮样本、反例、调用数、token范围及修复余量；用真实Milvus/Embedding与一条新订单客诉验证跨两个MCP，不重复仍适用的Business及模型能力证据。
4. 在单独特性分支完成验收、中文提交、合并与六SHA同步，更新PROJECT_STATE和同一份M20 handoff。完成后停止，不启动观测、前端或部署。
