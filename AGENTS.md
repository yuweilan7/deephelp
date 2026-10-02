# DeepHelp 实施约定

## 任务与上下文

目标是复刻PDF核心机制。先读 `docs/PROJECT_STATE.md` 和 `docs/ROADMAP.md`，再读当前模块规格、直接前置handoff、相关代码/测试。接口变化才读CONTRACTS，架构/来源争议才读ARCHITECTURE、DECISIONS、SOURCE_MAP及PDF对应页。普通实施不全量读取docs、infra和历史资料。

运行或检查真实依赖时读 `docs/LOCAL_SETUP.md`及 `.local/DEPENDENCIES.md`。模型先看 `.local/model-pool-summary.md`，只查询model-pool.json中本任务所需候选，不把完整模型目录、原始额度JSON或归档注入上下文。

M是能力目录，不是部署单元。每次一个可演示、可验收特性，用户未启动下一特性时不继续。已有 `planning/Mxx/PLAN.md` 就按它实施；没有时按 `docs/PLAN_MODULE_PROMPT.md` 写短计划。同一M的多个特性按序写在一份PLAN中，不新增S、日报、重复总结和空handoff。

## 分支、测试、合并

一个特性一分支、一主写者，不并行修改共享代码、根锁、迁移和测试数据。额外Agent仅在明确要求时参与，默认针对固定commit只读审查。

用户已授权正常实施及明确交付的文档改动通过验收后自动完成以下流程，不重复询问或留下手动收尾：

1. 确认工作区干净；切换main，`git fetch origin`、`git pull --ff-only origin main`，创建 `feature/<特性名>`。
2. 实现并通过特性验收及ROADMAP检查。审查差异，仅 `git add -- <明确文件>` 暂存本任务文件；检查 `git diff --cached --check`，中文commit，再 `git push -u origin <特性分支>`。
3. 确认特性已推送、工作区干净；切回main并pull，`git merge --no-ff <特性分支>`，使用中文合并说明。合并后跑规定检查与必要真实验证，通过后push main。基线变化先审查并重验受影响路径。
4. 切回特性，`git merge --ff-only main`、push、fetch；核对本地两个分支、对应远端跟踪分支及 `git ls-remote` 的两个远端SHA全部一致。保留已完成分支，最后回到干净main；下一特性另建分支。

提交标题 `类型(范围): 中文说明`。逐条检查退出码；冲突、验证/push失败或SHA不一致即停并报告，不能报完成。不覆盖用户未提交修改，不自动stash，不用reset --hard、clean -fd或force push。纯讨论、只读审查和纯规划不自动commit/push。最终报告改动、验证、提交说明及同步SHA。

## 真实依赖与预算

普通回归默认离线。依赖千问/腾讯云的特性，在明确目标、调用/token/费用上限和专用测试数据范围内，开发前检查依赖、完成后做最小真实接口/业务路径验证。已有适用授权/预算可复用，免费额度不是无限授权；缺配置或预算先报告并暂停。

额度、鉴权、网络、能力、预算或真实验收失败，停止该特性推进和合并。报告组件、脱敏错误/请求ID、已用预算（未知如实写）、已改文件和恢复事项。已明确失败不循环试错，不换模型/库/Mock宣布通过；恢复后重跑失败检查。

M02类型/fixture等纯离线特性不调用模型或远程库。真实MCP+合成工具只能证明协议。只读探针不运行P00 full；云服务重启、删除、恢复、故障演练、套餐变更及超预算付费调用另需明确授权。

## 工程边界与交接

- 保留Python3.14.7、根uv workspace、单一uv.lock、modules/deephelp-app和现有infra。MySQL是业务事实源，Redis/Milvus可重建，checkpoint不是账本。
- 同一套领域类型，DTO不含客户端/连接/锁。只有600 INTENT_RECOGNIZE调用主意图服务；权限、归属、幂等、版本、预算、超时、工具白名单和结果事实由代码与测试保证。
- 核心路线只读，业务写工具前必须M15。普通新消息不批准/恢复旧操作；副作用结果未知先对账，不盲目重放。
- 每M一份handoffs/Mxx.md，记录成果、实际命令/退出码、真实验证、未执行项与下游接口。主写者集成，凭证据更新PROJECT_STATE。
- 当前事实只写PROJECT_STATE，命令写运行入口，规格写机制/验收，handoff写证据；原始日志/旧提示/过程报告留Git历史或本地归档，不复制进当前文档。
- PDF、截图、内部地址、客户标识和凭据不提交公共仓库；只公开机制摘要、页码与合成数据。清理核对用途/引用，保留运行配置、数据、锁和来源原件。
