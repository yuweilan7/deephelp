# 项目真实状态

记录版本：M00-design-v1.2 / contract-0.1.2-draft / prompt-policy-v2。S06复核日期2026-09-13，输入commit：`ebf8f46bf85f893305afa4318039341f6f199c0d`；最早基础工程为15d9ac9。本文件记录已执行事实，不替代当前运行监控。

| 范围 | 状态 | 证据 / 限制 |
|---|---|---|
| 根 uv workspace | EXISTING_SCAFFOLD | 根 pyproject、uv.lock、README；Python 3.12.14 / uv 0.12 系列是原仓库约束 |
| P00 | HISTORICAL_ACCEPTANCE_REVIEWED | infra/reports/acceptance-summary.json 和客户端验收脚本已读；本轮未 SSH、未复测 |
| 云端 Milvus | ACCEPTED_EXPERIMENTAL（历史） | compose 上限 5 GiB；历史 4 维合成向量验证协议，不证明真实 embedding 效果或未来容量 |
| M00 | DESIGN_ACCEPTED | S06已覆盖核验S01–S05，补齐JSON样例、逐事件走查、单会话双视角和路径安全检查；项目Python3.12实际verify通过，文档测试29项中28通过/1环境跳过；证据见reports/M00/S06.md及validation-s06.json |
| M01–M21 | NOT_IMPLEMENTED | 只有规划文件；M01 不能因已有 workspace 就视作完成；M21 可选 |
| Windows 本地同步 / 原 PDF 与启动包归位 | COMPLETED_LOCAL | 已快进同步；PDF、原ZIP、42份解压文件逐字节/哈希校验复制到.local/references；原件保留、未公开上传；见reports/M00/windows-sync-v2.json |
| 业务 integration / live / e2e | NOT_RUN | 没有业务应用、模型授权或当前机器执行证据 |

## 有效技术基线

MySQL 是关系业务事实源；Redis 为可丢弃缓存；Milvus 为派生检索投影。本机应用/MCP Mock + 云端现有中间件。不得由旧模块 Prompt 反向切回 PostgreSQL 或重跑环境安装。

根锁文件目前只锁 workspace 本身；FastAPI、Pydantic、LangGraph、业务数据库驱动尚未成为应用锁定依赖。infra/client/requirements.lock.txt 是旧基础设施验收环境，不能直接当业务依赖锁。

MySQL checkpoint saver 尚未选定/验收。M01/M08 可明确使用非持久执行；M15 的持久恢复必须做兼容性与崩溃重放试验。此项不阻塞纯设计和异步骨架，但阻塞“已支持持久审批”的宣称。

## 下一步

本会话已完成M00设计验收，停在M00；M01–M21仍NOT_IMPLEMENTED。后续获授权后可执行M01离线异步骨架，随后M02发布唯一具体类型契约；不能把设计走查、文档工具测试或源文件归位当作业务运行通过。

S06是单会话双视角，没有其他写入Agent或独立会话审查。原PDF的SHA/78页与指定13幅页图已在本机核验；文档工具新增真实Windows junction反例通过。真实符号链接创建测试受系统权限限制跳过，单独保留限制，不计通过。M03模型/费用、M05真实容量、M15持久saver/审批并发/崩溃恢复仍为运行门禁。

共享 CONTRACTS、lockfile、数据库迁移和本文件由主集成会话维护。前置检查使用当前 commit，而不是永远固定本快照 SHA。历史验收报告与本轮新测试分开存放。
