# M21｜可选：AgentScope SOP 执行器与框架对照

**阶段：** F 迁移与对照

**前置：** M07, M15, M17, M19

**来源：** 原 PDF 文件页 67–69；框架比较依官方现行文档。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式

默认PLAN_MODULE，只设计/拆解本模块。使用[通用规划Prompt](../PLAN_MODULE_PROMPT.md)的六项交付格式。先读实仓AGENTS、CONTRACTS、PROJECT_STATE、DECISIONS及直接前置handoff；本文件不是完成证据。

本工作副本显式修订旧PG/部署/布局假设，采用现有MySQL、uv workspace与5GiB云Milvus受限实验；不重新部署、不自动付费、不把22阶段建成22个包。原PDF仅存本地，业务演示只用合成数据。

## 本模块目标
以最小代价学习第二个框架：只将SOPExecutorPort接一个AgentScope实现，在同一工具、数据、权限、业务存储和评测下对比。不是因为另一框架陌生就假定它更先进，也不是把主链重写一遍。

## 接口隔离
保留LangGraph主Pipeline、业务case/operation/approval的MySQL事实源、既有ModelGateway/ToolGateway或明确的兼容adapter。AgentScope负责有界ReAct和结构化SOPResult；不另存一套权威问题状态。确认框架message/tool/schema映射不会丢失call_id、权限、证据和预算。

查当前官方AgentScope文档关于ReAct、MCP、structured output、state_dict/Session与取消中断的语义。尤其区分“用户打断当前生成/async取消”与“可重启后恢复的持久审批”；不能因都有interrupt一词就认为M15安全属性自动等价。

## 审批与恢复方案
优先让主流程在执行确定操作前处理审批，AgentScope仅提出结构化行动建议；如果要把审批放入其内部循环，必须明确持久化点、重放行为、参数绑定和下游幂等，重新运行M15全部故障用例。没有证明等价之前保持只读功能对照，不松动主系统安全边界。

同一输入、同一模型/采样参数、同一SOP版本、同一工具Mock下比较两执行器。不要给一种框架更长Prompt/更多步骤或更强模型后说框架胜出。CPU/RAM、依赖/镜像体积、调用次数、延迟、完成率、取消/恢复、可观测性、实现复杂度都可记录；开发者主观体验与实测数据分开。

## 范围和验收
先跑M07的三个只读SOP，再选择一个M15受控审批场景。离线重放全量契约与故障测试，少量live探针核对真实协议。新增依赖默认只安装个人电脑隔离venv/profile，不扩大云端常驻服务。

若AgentScope更简洁且关键指标不退步，可以形成替换建议；若有安全/恢复差异，保留为只读对照adapter。无论哪种结果，交付接口差异表、实际测试/资源报告、学习笔记和迁移成本，而不是强行宣布胜负。

本模块是可选支线，不阻塞M20或主项目完成。只有实际完成并有证据时，项目介绍才写“实现并对比LangGraph/AgentScope”。

## 本轮交付

按通用规划Prompt给详细设计、3–7项DAG、每项完整独立Astra执行Prompt、分层验收和交接。路径：`docs/MODULES/M21.md`、`handoffs/M21.md`、`reports/M21/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
