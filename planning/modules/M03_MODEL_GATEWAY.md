# M03｜模型与 Embedding 网关、结构化输出和调用预算

**阶段：** B 最小闭环

**前置：** M01, M02

**来源：** 原 PDF 文件页 16–18、21–24、35–41、67–69。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式

默认PLAN_MODULE，只设计/拆解本模块。使用[通用规划Prompt](../PLAN_MODULE_PROMPT.md)的六项交付格式。先读实仓AGENTS、CONTRACTS、PROJECT_STATE、DECISIONS及直接前置handoff；本文件不是完成证据。

本工作副本显式修订旧PG/部署/布局假设，采用现有MySQL、uv workspace与5GiB云Milvus受限实验；不重新部署、不自动付费、不把22阶段建成22个包。原PDF仅存本地，业务演示只用合成数据。

## 本模块目标
把模型供应商差异挡在一个可测的网关后面，确认“能聊天”“能工具调用”“能稳定输出 schema”“能生成指定维度向量”是四项分别验收的能力。

## 接入前核验
我的千问免费 API 是待核实条件，不是无限额度。查当前账号/地域/模型的实际资格、剩余额度、有效期和用完即停开关；不能自动开通付费或切到收费模型。聊天与 Embedding 的 endpoint、model、权限、限流分别配置。未经授权，不运行大批量盲测或自动重试预算穿透。

选一个已验证支持工具调用和结构化输出的 chat 模型，再选 API Embedding；优先尝试可用的 1024 维以接近原文，但 schema 维度以实测为准。API 向量不等于 BGE-M3，不能把两个模型或维度混进一个 collection。保存 provider/model/revision/dimension/normalization 的 embedding_signature，变更必须新建 collection/reindex 并切换版本。

## 网关契约与实现要求
ChatPort 返回结构化结果、usage、finish_reason、provider_request_id；EmbeddingPort 返回有序向量列表及签名。模型侧不支持严格 schema 时，可用受限校验/一次修复流程，但计入总预算，修复失败要返回可判定错误，不能悄悄接受半截 JSON。tool calling 必须走实际工具协议的结构化字段，而不是正则抓自然语言指令。

统一超时、429/5xx 分类、指数退避加有限抖动、最大重试与总 deadline；只能选择一个层做主要网络重试。使用请求预算对象记录真实尝试次数，缓存命中不假记 API 费用。提供 FakeGateway 和可录制脱敏 fixture，禁止把密钥、完整敏感 prompt、原始公司数据录进 cassette。

Embedding 缓存以规范化文本+signature 为键，支持批次和容量上限；不要在云盘无限保存响应。模型超时、余额耗尽、网络不可达、tool schema 不支持时提供清楚的降级信号，交由上游问人/模板返回，不自动更换业务含义。

## 验收
最小 live probe 分别测试普通 chat、JSON/schema、单个工具调用、Embedding 批次顺序和长度；检查 NaN/维度异常、空文本、重复输入。live probe 调用量有明确上限。注入 429/timeout/截断JSON/未知工具名，验证不形成重试风暴，不把错误字符串当向量或正常答案。

交付 providers 配置示例（无密钥）、模型能力矩阵、环境/版本签名、网关实现子任务、单测和受控 live 脚本。未完成 live 时写 PENDING_LIVE，不能写“千问兼容所以都支持”。本轮不下载 BGE 或大语言模型，不装训练框架。

## 本轮交付

按通用规划Prompt给详细设计、3–7项DAG、每项完整独立Astra执行Prompt、分层验收和交接。路径：`docs/MODULES/M03.md`、`handoffs/M03.md`、`reports/M03/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
