# M09 实施计划

> 现行验收以[AGENTS](../../AGENTS.md#按改动范围验收)和[ROADMAP](../../docs/ROADMAP.md#按影响选择验收)为准。下文旧计划/记录中的全套live、四能力、四组或main复验不是当前默认门禁；历史证据保留。

判断：PROBE_FIRST → DIRECT；单个特性 `feature/m09-hybrid-retrieval`，基线 `00ab147a0dbb2b5c417a89da6b6ed2f61a0de32b`。

1. **这次做成什么**：在现有 Milvus 上以同一合成语料、原始 query、候选预算比较 Dense / 服务端中文 BM25 / WeightedRanker；输出原始分数、融合来源、排序与分类对照。dev 选权重，冻结后仅运行独立 test；不预设融合获胜，不启动 M10。
2. **从哪里改**：复用 M05 CorpusPreview、DenseImporter、manifest、容量门禁、DTO 和端口；新增 HybridScope、可追踪评分及 HybridRetrieverPort、Milvus sparse schema 和可复现 CLI。M08 在原 600 服务选择新检索器，保留旧指针兼容。新集合重建，不改原 Dense 集合；不新增依赖或业务库迁移。
3. **怎样证明**：先 health、Embedding 内容、analyzer / BM25 / hybrid_search 能力探针；再导入回读、三路线同 query、归一化逐分核对、dev 选择/test 冻结、真实一次 schema 分类、删除/恢复/版本切换与过滤。离线覆盖取消、超时、版本、split、评分边界与旧消费者；按 ROADMAP 在特性与 main 完整检查。CLI现入口 `python -m deephelp_app.evaluation.hybrid_cli --help`；旧路径已迁移，旧验收要求按顶部现行规则执行。
4. **什么时候停**：使用既有专用云实验环境和模型池，任务预算/超时/零重试留 `.local/m09`。正常返回后的解析/兼容错误自主修复；不可恢复鉴权、网络、服务或能力不足即停，不合并。只写合成意图集合，禁止跨查历史/事件集合；容量拒绝即停。
5. **怎样交付**：检查差异、中文提交、推送特性、合并并复验 main、同步特性与远端 SHA，最后干净 main。更新 CONTRACTS、运行入口、handoffs/M09.md 和 PROJECT_STATE；原始报告只留 `.local/m09`。
