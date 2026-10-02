# M05 实施计划

判断：`PROBE_FIRST` → `DIRECT`。基线 `73e9930726c253fa790aa036b415662370a5c236`，分支 `feature/m05-dense-ingest`。

1. **这次做成什么**：版本化合成客诉 reference 语料 → 校验预览 → 真实 Embedding/Milvus 幂等导入 → Dense 命中与按意图候选 → 独立 dev Recall@K。提供 JSONL/CSV 和无表头 XLSX 明确列映射；不实现最终意图接管、BM25/Hybrid 或 M06。
2. **从哪里改**：复用 M02 registry/cases、M03 EmbeddingPort/Signature/ExecutionBudget，在唯一 domain/models.py 和 ports.py 增量发布检索 DTO/Port；新增 corpus、dense、Milvus adapter 与命令入口。SDK加入应用依赖及唯一 uv.lock，无业务数据库迁移；集合限 m05_intent_，不动 p00。
3. **怎样证明**：真实前置 health、Embedding 与容量已通过（Milvus2.6.23/PyMilvus2.6.17）；离线覆盖格式错误、split/近义组隔离、signature、幂等/未知提交续跑、容量与取消；真实验证专用集合 schema/FLAT COSINE、重复导入、过滤、版本切换/回退、canary记录删除/恢复及服务重启持久性、dev内容指标。特性/main 均跑 ROADMAP 检查；入口命令落 LOCAL_SETUP。现有专用云实验环境维护已获用户授权，并同步更新AGENTS。
4. **什么时候停**：采用小批次、串行、有限请求超时与共享调用/token/费用预算，预算及原始报告仅存 .local/m05。每次新批次/建集合复查 P00 磁盘和 Milvus 内存余量；不循环建集合。真实组件不可恢复失败、容量不足、冲突、验证或推送失败即停；不扩展到非项目资源。
5. **怎样交付**：审查明确文件并中文提交，推特性，合并 main 后完整检查与真实最小复验，再双分支同步并核对远端 SHA，最终干净 main。更新 M05 handoff、PROJECT_STATE、数据字典/CONTRACTS 和运行入口。FP16对照为可选，未验证不启用；不能以重连冒充服务重启。

重启验收范围增量：实际复现embedded etcd选举启动故障，临时选举调参已撤回。冷备原数据/配置后，将原etcd3.5.23元数据目录改为同机独立小容器，Compose等待健康再启动原Milvus；不改MySQL/Redis、不新购资源。先对已有两个M05集合做完整只读验证，再验真正重启和受影响运维脚本；架构裁决见ADR021。
