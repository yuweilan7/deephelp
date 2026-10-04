# M27 实施计划

判断：`PROBE_FIRST`。就绪：`READY`（M25/M26已交付；自托管SeaweedFS/组合容量仍须首试验，不代表平台已验收）。规格见 [M27](../../docs/MODULES/M27_LANGFUSE_PLATFORM.md)。本次仅更新前置状态，未启动M27。

1. **这次做成什么：** 完成M27全部特性：先锁定Langfuse服务/SDK与SeaweedFS稳定镜像，隔离启动必要Web/Worker/PostgreSQL独立库/ClickHouse/专用队列Redis/单机SeaweedFS；合成observation经过对象存储/Worker真实入库并读回。首项验收交付后依据结果更新本PLAN，继续恢复与完整组合容量特性。
2. **从哪里改：** M23/M25/M26 handoff、LOCAL_SETUP/.local/DEPENDENCIES、infra与根锁；按Langfuse官方SeaweedFS接入示例配置S3身份/path-style，明确持久对象与metadata目录、私有端点/卷和实际桶/所需操作。只使用现有服务器，不购买COS/RDS或托管存储，不引入Kubernetes。`feature/m27-langfuse-storage-proof`从main建立。
3. **怎样证明：** SDK发送→SeaweedFS/队列→Worker→ClickHouse→API/浏览器同一合成trace/usage读回，模型0次；容器重建后对象/metadata仍可读，停止/恢复对象存储与观测超时不污染业务。后续测含SeaweedFS的全组合真实同时峰值、后台merge/积压/索引训练、OOM/磁盘/延迟与隔离恢复；阈值在本次试验前写入口。
4. **什么时候停：** 自托管S3操作/版本不兼容、对象/metadata持久化失效或容量无法满足必要业务时停止；不改购托管存储、不省略必需存储、不退旧版本、不卸载旧栈/降模型以凑通过。
5. **怎样交付：** 真实兼容、平台读回及实际组合容量/恢复证据写M27 handoff/STATE，未测范围明确保留；各特性分别合入main并按AGENTS同步，在同一会话继续下一项。M27全部特性交付后停止，M28业务接入由下一M负责；不在规划阶段创建handoff。
