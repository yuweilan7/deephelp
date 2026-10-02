# 项目真实状态

本文件只记录当前事实；计划、聊天结论和待执行命令不算完成证据。

| 范围 | 状态 | 证据与限制 |
|---|---|---|
| 根 uv workspace | `EXISTING_SCAFFOLD` | Python 3.14.7；根workspace/单一`uv.lock`已存在；业务依赖位于M01成员包，根项目不打包 |
| P00 基础设施 | `HISTORICAL_ACCEPTANCE_REVIEWED` | 见 `handoffs/P00.md` 与 `infra/reports/`；本轮未重新连接云机 |
| M00 架构 | `DESIGN_ACCEPTED` | 架构、契约、ADR、来源索引与验收场景已形成；见 `handoffs/M00.md` |
| M01 | `IMPLEMENTED_OFFLINE_VERIFIED` | `modules/deephelp-app/` 已实现最小异步骨架；48 项离线测试通过，1 项 live 配置检查默认跳过；见 `handoffs/M01.md` |
| M02–M14、M16–M18 | `NOT_IMPLEMENTED` | 核心路线仅有规格；2026-10-02完成路线/流程修订，本轮不启动M02 |
| M15、M19 | `HARDENING_NOT_IMPLEMENTED` | 独立加固；只读核心可后移，业务写工具启用前必须M15 |
| M20 | `OPTIONAL_NOT_IMPLEMENTED` | 物流迁移不是客诉复刻前置 |
| M21 | `OPTIONAL_NOT_IMPLEMENTED` | AgentScope 对照支线，不阻塞主项目 |
| 业务 integration / live / e2e | `NOT_RUN` | 尚无业务应用与真实模型运行证据 |

## 当前基线

MySQL 保存业务事实；Redis 是可重建缓存；Milvus 是可重建检索投影。主路线使用 FastAPI/Pydantic/LangGraph，保留单一 uv workspace。M01 已创建首个业务包、离线异步骨架、共享客户端、占位 converse API、trace、fake 适配器与异步实验；M02 将在同一套最小类型上发布正式契约。M01 未安装 LangGraph，未连接真实数据库或调用真实模型。

2026-09-20 已将工程解释器基线迁移到 Python 3.14.7：根 `.python-version`、根/成员 `requires-python`、Ruff、mypy、CI 与根 `uv.lock` 已同步；M01 在 3.14.7 下的离线检查仍为 48 passed、1 skipped。P00 验收报告保留当时环境版本，不冒充本次复验。

原 PDF 已由用户放在本机 `docs/`，不会提交公共仓库；其身份与页码索引见 `docs/SOURCE_MAP.md`。仓库中的模块规格以 `docs/MODULES/M00_*.md` 到 `M21_*.md` 为准。

## 2026-10-02 路线/流程修订

已核对docs全部文件与78页PDF，来源哈希匹配。路线分为M08闭环、只读核心深化、规模扩展和独立加固/可选扩展；见[ROADMAP](ROADMAP.md)。修正M16/M17的M15前置、M02协议分期、分支合并流程和真实依赖失败停止规则。M02代码、计划、迁移及handoff均未创建。

本轮M01离线回归仍为48 passed、1 skipped；Ruff检查/格式和mypy通过，命令与退出码见ROADMAP。PATH中裸uv为0.12.10，不满足根要求；现有`py -3.11 -m uv`为0.12.13，项目虚拟环境仍为Python3.14.7。本轮未调用千问、未连接/修改腾讯云中间件；其当前可用性未复验。

后续补充并统一 AGENTS、ROADMAP 和 README 的自动 Git 交付规则：特性分支先 add/中文 commit/push，再合并 main 并复验，最后同步本地与远端两个分支。用户已明确要求在现有 `feature/docs-roadmap-workflow` 上实际验证；交付前重跑 Python 版本、Ruff检查/格式、mypy、pytest和差异检查均退出0，仍为Python3.14.7、48 passed/1 skipped及253条既有弃用警告。Git实际执行结果以交付回复中的命令结果和远端SHA核对为准。

## 尚未解除的真实验证

- M03：实时模型/额度核验、调用/token/费用硬上限与 embedding signature（已有用户授权按既有范围复用）
- M05：真实 Milvus 集合、容量和索引验证
- M15：MySQL 持久 saver、审批并发、下游幂等/查询与崩溃恢复

这些门禁不阻塞 M01 的离线实现。共享契约、锁文件、迁移和本文件只由当前模块主写者串行更新。

M01 历史实施轮由同一主写者实施、验证与集成，更新本文件的依据为 `handoffs/M01.md` 中实际命令及退出码。离线 integration 仅使用合成数据和 loopback HTTP；离线应用 e2e 不计作上表的业务 integration/live/e2e。
