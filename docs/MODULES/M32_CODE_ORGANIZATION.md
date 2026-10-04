# M32｜能按职责阅读的代码结构与有证据的清理

**来源：** 用户2026-10-05明确要求；工程与学习改进，不新增PDF复刻结论。
**前置：** 已交付M19分层、M30正式路线和M31发布边界；见[M19](../../handoffs/M19.md)、[M30](../../handoffs/M30.md)、[M31](../../handoffs/M31.md)。
**实施：** [唯一PLAN](../../planning/M32/PLAN.md)。本轮只交付规格/计划，未实施结构迁移或全项目删除。

## 要解决什么

现有app的adapters有32个Python文件、application有21个；models.py约1786行，conversation.py约1035行，event_cluster.py约913行。tools的evaluation有31个文件、probes有26个。大层方向正确，但一个目录混合模型、数据库、MCP、观测、资产等不同职责；仅增加目录树说明不足以解决定位困难。数量是规划时基线，不是按行数机械拆文件的门槛。

交付目标是读者能从“收到一条消息”找到流程，再按模型/检索/持久化/审批职责深入；实际删除已确认无消费者的废文件、重复测试和忽略目录过程副本。保留根workspace、三包、唯一uv.lock、Python3.14.7、一套领域类型/引擎及现有infra。

## 三个顺序特性

| 顺序 | 可独立交付的结果 | 主要入口 |
|---|---|---|
| 1 | 唯一类型按业务概念拆分，会话用例按编排/状态推进/终态保存拆分 | domain/models.py、application/conversation.py、event_cluster.py |
| 2 | 适配器、装配和工具按实际子系统归组，命名直接表达责任 | adapters、bootstrap、tools的evaluation/probes/learning、services |
| 3 | 清理无效文件与冗余测试，补源码导航和真实职责说明 | 三包tests、docs/learning/README.md、根/包README、忽略目录的精确对象 |

每项一个特性分支、相关验收和Git交付；全部完成才报告M32完成。第3项必须执行已确认可删对象的删除，不能只留下盘点清单。

## 目录与命名决定

保持domain/application/adapters/api/bootstrap/resources上层边界，在内部按能力归组。下表是迁移候选，实施先核对实际依赖图；没有实际代码/消费者不建空目录。

| 当前混合入口 | 建议职责组 | 必须解释的责任 |
|---|---|---|
| domain/models.py | models/common、conversation、entities、intents、retrieval、tools、approval、memory、release、business | 每类只定义一次，DTO不带连接/锁；依赖由公共值类型向具体业务类型单向流动 |
| application/conversation.py及event_cluster.py | conversation编排、events归属、memory状态、retrieval决策、sop执行、approval恢复 | 编排先呈现主步骤；复杂辅助逻辑移到有名字的组件，不复制正常/恢复两套引擎 |
| adapters/gateway.py、providers.py | model_gateway | HTTP请求/响应适配、schema解析、计量各自有明确入口 |
| ledger/cases/history/postgres、Redis/Milvus文件 | persistence、memory_projection、retrieval | PG事实事务、可重建投影、召回各有边界，不把所有store堆到同一目录 |
| mcp/network/business_client/retrieval协议文件 | mcp/business、mcp/retrieval、mcp/shared | 客户端、可信委托、双端schema和业务事实分别定位 |
| native_observation/langfuse_query/telemetry/prometheus | observability | 埋点、脱敏、查询、指标；诊断不是业务事实源 |
| release/runtime资产文件 | releases | 不可变验证、引用、active CAS与运行绑定，不成为泛化utils |
| tools/evaluation、probes | 按检索训练、质量评测、反馈发布、迁移恢复、协议验证归组 | 正式消费和显式实验分开；一个命令只有一个实现 |

具体名字以清晰、少层级和真实责任为准，不以目录最多8个文件等数字强拆。稳定的公开import可用包__init__显式导出唯一类型/函数；内部调用改为新责任入口。临时兼容别名须列消费者与移除条件，无消费者就清除，不能留两套实现。避免service/manager/common/utils成为新的杂物箱。必要目录说明写“负责什么、不负责什么、入口、调用者、失败后谁处理”，不为每个文件制造重复README。

## 清理必须凭用途

先读取Git跟踪/忽略状态、大小、进程/PID/锁、静态import、动态装配、CLI、pytest节点、包资源、运行配置、Markdown链接和不可变发布引用。对每个删除对象记录所有者、消费者、保护引用、是否仍运行、替代证据、精确绝对路径及重解析点核对；最终交接只保留必要依据，不提交全盘文件/客户资料清单。

删除范围包括本轮确认的重复过程导出、已解决失败整份转储、无消费者的一次性脚本/构建/渲染、过时副本和重复测试。重点重新核验过去暂留的.local/tmp/m30、.local/tmp/m31-runtime-package和.local/m31/content-on.json；历史“可删”描述不能代替当前引用核查。.gitignore不是保留或删除的证明，不按文件名/M编号批删，也不把未知对象全报成策略阻塞。

保留来源原件、凭据/身份、运行配置、根锁、活动/previous/保留版本、manifest闭包、累计预算、有效锁/进程、待审批/UNKNOWN、唯一恢复/失败诊断和审计引用。旧MySQL/Tempo已退役不表示冷备/源数据/旧审批转换器都可删；核对M30恢复消费者再判断。删除旧源码须同步检查仍需读取历史状态的迁移/恢复入口。

正常清理已获用户明确授权。优先精确单文件删除，再删除空目录；不新增全盘清理脚本/全局hook/定时任务，不自动stash或强制重置。真实工具拒绝时说明被拒绝的精确动作/原因；允许改成可独立核验、范围更小的正常操作，不能换工具绕过同一拒绝。无法核实的对象暂留并说明具体消费者或缺口，继续清理已确认对象。

## 验收与退出

迁移前保存适用离线基线和公开入口列表，迁移后核对CLI/动态import/资源/安装wheel及运行指纹。共享类型/装配迁移较广时做一次必要完整离线回归，后续只复验改变的范围；现有失败先定位，不能靠删测试换PASS。为每个删除的重复用例逐项映射仍保留的独立断言，特别保留归属、幂等、金额、权限、取消/并发、UNKNOWN、协议错误和不可变发布反例。

演示沿当前请求→会话→归属→意图→SOP→事实/证据路径找到具体组件，并在独立安装中使用原公开入口。代码组织等价改动模型0次；如实际改变请求/解析/决策，先停止“纯重构”归类，按ROADMAP补对应内容证据。新源码指纹变化不改写旧清单；当前云安装可继续保留，若本M另交付新正式安装，必须明确执行精确完整内容发布。

退出必须给出实际迁移映射、断言保留、删除结果/暂留理由、入口验证和回滚方式。已提交迁移用revert恢复，忽略目录只删除已证明可重建/重复对象，唯一资料不进入删除范围；不承诺无法恢复的任意本地文件删除。全部特性交付后停止。
