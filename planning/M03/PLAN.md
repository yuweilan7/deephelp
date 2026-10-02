# M03 实施计划

判断：`PROBE_FIRST`。基线 `96347d4e8d1cbfa382dad8a2ff5aa34a7f0b3b6e`，特性分支 `feature/m03-model-gateway`，一名主写者。

1. **这次做成什么**：交付有界千问 Chat/schema/tool/Embedding 网关，四项能力分别真实验收；修复全零 Embedding index 兼容问题。用户追加统一 Python3.14.7 入口及文档。M06 MCP、M08 业务 converse、云库和训练框架不在本轮范围。
2. **从哪里改**：复用根 workspace、单一 uv.lock、httpx、ExecutionBudget 和唯一 domain/models.py；新增 ChatPort/EmbeddingPort、provider 配置、网关/FakeGateway/合成 fixture、结构录制和受控 live 脚本。新增 jsonschema 与开发类型桩；统一启动器/当前文档，不改 infra 或迁移。
3. **怎样证明**：合成批次/单输入对照定位字段，只有选定模型启用全零索引适配。离线覆盖 429/5xx、timeout/取消、截断 JSON/一次修复、未知工具、NaN/维度/索引、缓存/批次/重复输入、共享调用/token/费用预算。按 ROADMAP 完整检查并检查文档；特性四请求验收，无代码变化的合并用 chat/embed 两请求复验。
4. **什么时候停**：用户明确授权合计12次请求、20,000输入加输出token，费用上限随后放宽至¥100；本轮累计文件采用更小¥1上限。仅合成短文本，live零重试、不切模型。正常返回的本地解析/断言/适配异常先脱敏诊断并在原预算内自主修复；确认额度/鉴权/网络/能力不可用、超预算或无法范围内恢复时停止。预算不归零，云服务操作另需授权。
5. **怎样交付**：四项真实验收和全工程离线检查通过后按 AGENTS 中文提交、推送特性、合并复验main，快进保留特性分支，核验六个SHA并回到干净main；更新唯一handoff、STATE、接口/运行文档，不启动M04。

探针结论：全部向量1024维、finite=true，失败为所有index=0；单输入与批次对应位置余弦0.99819，其他位置约0.62430。选定模型显式按响应位置适配，其他坏索引仍拒绝。工具原生字段有效时兼容finish_reason=stop。revision未知保持null，客户端显式L2；签名变化要求新集合/reindex。证据见[M03交接](../../handoffs/M03.md)，当前事实只维护PROJECT_STATE。
