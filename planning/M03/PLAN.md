# M03 实施计划

> 现行验收以[AGENTS](../../AGENTS.md#按改动范围验收)和[ROADMAP](../../docs/ROADMAP.md#按影响选择验收)为准。下文旧计划/记录中的全套live、四能力、四组或main复验不是当前默认门禁；历史证据保留。

判断：`PROBE_FIRST`。基线 `96347d4e8d1cbfa382dad8a2ff5aa34a7f0b3b6e`，特性分支 `feature/m03-model-gateway`，一名主写者。

1. **这次做成什么**：交付有界千问 Chat/schema/tool/Embedding 网关，四项能力分别真实验收；修复全零 Embedding index 兼容问题。用户追加统一 Python3.14.7 入口及文档。M06 MCP、M08 业务 converse、云库和训练框架不在本轮范围。
2. **从哪里改**：复用根 workspace、单一 uv.lock、httpx、ExecutionBudget 和唯一 domain/models.py；新增 ChatPort/EmbeddingPort、provider 配置、网关/FakeGateway/合成 fixture、结构录制和受控 live 脚本。新增 jsonschema 与开发类型桩；统一启动器/当前文档，不改 infra 或迁移。
3. **怎样证明**：合成批次/单输入对照定位字段，只有选定模型启用全零索引适配。离线覆盖 429/5xx、timeout/取消、截断 JSON/一次修复、未知工具、NaN/维度/索引、缓存/批次/重复输入、共享调用/token/费用预算。按 ROADMAP 完整检查并检查文档；特性四请求验收，无代码变化的合并用 chat/embed 两请求复验。
4. **什么时候停**：合成文本探针使用累计运行上限，恢复不归零；不可恢复的真实依赖问题按AGENTS停止，正常返回后的本地异常先诊断修复。临时参数和原始运行记录留.local/Git历史，不作为后续任务模板。
5. **怎样交付**：四项真实验收和全工程离线检查通过后按 AGENTS 中文提交、推送特性、合并复验main，快进保留特性分支，核验六个SHA并回到干净main；更新唯一handoff、STATE、接口/运行文档，不启动M04。

探针结论：全部向量1024维、finite=true，失败为所有index=0；单输入与批次对应位置余弦0.99819，其他位置约0.62430。选定模型显式按响应位置适配，其他坏索引仍拒绝。工具原生字段有效时兼容finish_reason=stop。revision未知保持null，客户端显式L2；签名变化要求新集合/reindex。证据见[M03交接](../../handoffs/M03.md)，当前事实只维护PROJECT_STATE。

## 顺序文档特性

已完成`feature/m03-quality-first-policy`的接口限制审查；历史过程留Git。当前特性纠正文档中的临时金额/用量叙述及模型使用规则，不重复复制上次计划。

判断：`DIRECT`。基线`ea2d73bf1f986e4b1399989ad27b62c1e3d00c42`，分支`feature/m03-learning-first-docs`，一名主写者。

1. **这次做成什么**：以学习效果为默认目标，明确候选池与账号余额可用于常规开发/验收，精简上下文。
2. **从哪里改**：修订现有约定、状态/运行/规划入口、风险、M03规格/矩阵/README及唯一PLAN/handoff；不新增文档，不改代码/接口/运行记录。
3. **怎样证明**：检查规则一致性、历史金额/用量无残留、相对链接及运行入口；执行ROADMAP完整离线检查，无需真实调用。
4. **什么时候停**：Git或检查失败停止交付；不实现自动切换、不启动后续模块、不操作云服务。
5. **怎样交付**：中文提交、推送、合并main并复验；保留特性分支，核验六个SHA一致后回到干净main。
