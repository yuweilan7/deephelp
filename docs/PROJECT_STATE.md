# 项目真实状态

记录版本：M00-design-v1.1 / prompt-policy-v2。此次复核输入commit：`4724a386ceecf62efe318569526103e3233d5db6`；最早基础工程为15d9ac9。本文件记录已执行事实，不替代当前运行监控。

| 范围 | 状态 | 证据 / 限制 |
|---|---|---|
| 根 uv workspace | EXISTING_SCAFFOLD | 根 pyproject、uv.lock、README；Python 3.12.14 / uv 0.12 系列是原仓库约束 |
| P00 | HISTORICAL_ACCEPTANCE_REVIEWED | infra/reports/acceptance-summary.json 和客户端验收脚本已读；本轮未 SSH、未复测 |
| 云端 Milvus | ACCEPTED_EXPERIMENTAL（历史） | compose 上限 5 GiB；历史 4 维合成向量验证协议，不证明真实 embedding 效果或未来容量 |
| M00 | DESIGN_READY / REVIEW_PENDING | 架构、语义契约、来源差异、六个子任务及验收规格已交付；22模块和规划策略v2已复核；最终S06设计验收仍待执行 |
| M01–M21 | NOT_IMPLEMENTED | 只有规划文件；M01 不能因已有 workspace 就视作完成；M21 可选 |
| Windows 本地同步 / 原 PDF 与启动包归位 | COMPLETED_LOCAL | 已快进同步；PDF、原ZIP、42份解压文件逐字节/哈希校验复制到.local/references；原件保留、未公开上传；见reports/M00/windows-sync-v2.json |
| 业务 integration / live / e2e | NOT_RUN | 没有业务应用、模型授权或当前机器执行证据 |

## 有效技术基线

MySQL 是关系业务事实源；Redis 为可丢弃缓存；Milvus 为派生检索投影。本机应用/MCP Mock + 云端现有中间件。不得由旧模块 Prompt 反向切回 PostgreSQL 或重跑环境安装。

根锁文件目前只锁 workspace 本身；FastAPI、Pydantic、LangGraph、业务数据库驱动尚未成为应用锁定依赖。infra/client/requirements.lock.txt 是旧基础设施验收环境，不能直接当业务依赖锁。

MySQL checkpoint saver 尚未选定/验收。M01/M08 可明确使用非持久执行；M15 的持久恢复必须做兼容性与崩溃重放试验。此项不阻塞纯设计和异步骨架，但阻塞“已支持持久审批”的宣称。

## 下一步

先执行 planning/M00/S06_ACCEPTANCE_HANDOFF.md 的复核，将实际结果和 commit 写进 handoff。通过后由集成会话把 M00 改为 DESIGN_ACCEPTED，开始 M01，随后 M02 发布具体类型契约。不得一次性把后续模块状态设为完成。

共享 CONTRACTS、lockfile、数据库迁移和本文件由主集成会话维护。前置检查使用当前 commit，而不是永远固定本快照 SHA。历史验收报告与本轮新测试分开存放。
