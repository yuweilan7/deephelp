# 项目真实状态

本文件只记录当前事实；计划、聊天结论和待执行命令不算完成证据。

| 范围 | 状态 | 证据与限制 |
|---|---|---|
| 根 uv workspace | `EXISTING_SCAFFOLD` | Python 3.12；根 `pyproject.toml` 与 `uv.lock` 已存在，尚无业务依赖 |
| P00 基础设施 | `HISTORICAL_ACCEPTANCE_REVIEWED` | 见 `handoffs/P00.md` 与 `infra/reports/`；本轮未重新连接云机 |
| M00 架构 | `DESIGN_ACCEPTED` | 架构、契约、ADR、来源索引与验收场景已形成；见 `handoffs/M00.md` |
| M01-M20 | `NOT_IMPLEMENTED` | 仅有模块规格；下一步是规划并实施 M01 |
| M21 | `OPTIONAL_NOT_IMPLEMENTED` | AgentScope 对照支线，不阻塞主项目 |
| 业务 integration / live / e2e | `NOT_RUN` | 尚无业务应用与真实模型运行证据 |

## 当前基线

MySQL 保存业务事实；Redis 是可重建缓存；Milvus 是可重建检索投影。主路线使用 FastAPI/Pydantic/LangGraph，保留单一 uv workspace。M01 创建首个业务包和离线异步骨架，M02 在同一套最小类型上发布正式契约。

原 PDF 已由用户放在本机 `docs/`，不会提交公共仓库；其身份与页码索引见 `docs/SOURCE_MAP.md`。仓库中的模块规格以 `docs/MODULES/M00_*.md` 到 `M21_*.md` 为准。

## 尚未解除的门禁

- M03：真实模型授权、费用上限与 embedding signature
- M05：真实 Milvus 集合、容量和索引验证
- M15：MySQL 持久 saver、审批并发、下游幂等/查询与崩溃恢复

这些门禁不阻塞 M01 的离线实现。共享契约、锁文件、迁移和本文件只由当前模块主写者串行更新。
