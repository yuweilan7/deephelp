# 启动包导入与修订记录

源ZIP SHA-256：0d338af8683a9dab4c5d4f629aa4c9bd37ccc2932d23ca2792024a9d01254dc1。
原PDF SHA-256：4199ec4d9fcd35f88039868f9c878d3f8d0568bf25ff085fa4d8907d05aa5375。
远程输入commit：15d9ac9cd03251f7c32242cbdf1279abbffb8df9。

| 原包材料 | 去向 | 原因与修改 |
|---|---|---|
| 00_START_HERE.md | 根00_START_HERE.md（重写当前入口） | 原名核对；移除“现在重装P00”，改为当前进度和按需读取 |
| templates/AGENTS.template.md | 根AGENTS.md | 精简常驻规则，更新MySQL/已有workspace/5GiB实验，不内嵌22份Prompt |
| docs/01_SOURCE_MAP.md | docs/SOURCE_MAP.md + DECISIONS.md | 保留核心页码证据；原图冲突与工程改造显式区分，不转录私有数据 |
| docs/02_TECH_ROUTE.md | ARCHITECTURE/DECISIONS | 改为实仓MySQL、原workspace与独立持久saver门禁 |
| docs/03_RESOURCE_BUDGET.md / resource_budget.json | ARCHITECTURE/DECISIONS/修订M19 | 不把旧4GiB、云API/worker、目标磁盘配额当既有事实 |
| docs/04_DAG_AND_GATES.md / manifest.json / dependency_graph.mmd | planning/README.md、manifest.json、dependency_graph.mmd | 保留22模块直接依赖；P00改为历史handoff，不重置进度 |
| docs/05_WEB_SOURCES.md | DECISIONS查证入口 | 留当前涉及的官方/作者资料；安装时仍核对实际锁，不批量安装 |
| prompts/M00–M21 | planning/modules/同名文件 | 按需工作副本：保留专属范围/失败案例，公共底座去重，修订PG/拓扑/单pyproject和M15后端假设 |
| prompts/P98_SECOND_LEVEL_ROUTER.md | planning/PLAN_MODULE_PROMPT.md | 固定通用模板，自动识别本轮模块，实际读取远程状态与前置 |
| prompts/P99_ASTRA_IMPLEMENTER.md | planning/IMPLEMENT_TASK_PROMPT.md | 保留路径、权限、预算、测试/交接纪律；不能代替具体子任务 |
| HANDOFF/CHILD_TASK/PROJECT_STATE模板 | handoffs、当前STATE和六个M00子任务；planning/templates/HANDOFF.md | 实例化有用模板，不把NOT_STARTED初始状态覆盖既有P00 |
| EVALUATION_MANIFEST模板 | 暂留原下载包 | M02/M17按真实类型和数据发布，M00不冒充已有数据资产 |
| 22模块合订本 | 不入工程 | 与单模块重复、上下文过大；原下载包保留，可人工查阅 |
| 原P00环境执行Prompt | 不作为当前工程执行入口 | 已有环境，照抄可能重装/改坏；现入口handoffs/P00.md |
| PACKAGE_VALIDATION.json / SHA256SUMS.txt | 保留原包，不冒充新工程校验 | 字节修改后旧校验不再适用；新检查另存reports/M00 |
| 原始PDF/截图 | 仅.local/references/ | 脚本按hash复制；公开仓库仅存source-manifest和页码摘要 |

本次不会覆盖infra历史文件、根pyproject.toml或uv.lock。22份模块不是22个包，也不是每轮自动读入的提示词；AGENTS只导航到当前任务/直接前置。

Windows本地目录尚未由本会话操作。远程提交与本地checkout是两处状态；本地同步脚本仅ff-only拉取与私有PDF复制，不删除原文件、不自动提交、不推送本地无关修改。
