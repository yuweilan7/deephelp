# M03｜模型与 Embedding 网关、结构化输出和调用预算

**阶段：** B 最小闭环

**前置：** M01, M02

**来源：** 原 PDF 文件页 16–18、21–24、35–41、67–69。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式（v2）

默认 MODE=PLAN_MODULE。实际读取 AGENTS、CONTRACTS、PROJECT_STATE、当前相关代码/测试与直接前置 handoff，按[通用规划 Prompt](../PLAN_MODULE_PROMPT.md)先选择 DIRECT / DECOMPOSE / PROBE_FIRST / VERIFY_EXISTING，再给最小充分设计和实施任务；不强制拆分。明确要求实施时按本轮授权执行，不继续生成下一层规划。

原图按模块页码读取 `.local/references/deephelp-original.pdf` 或本轮 PDF 附件；GitHub 访问不包含被忽略的本地文件。已核对的原图不必每个 S 重读，旧解压稿仅为历史来源。保留现有 MySQL、uv workspace 和已记录的云 Milvus 实验，不重建 P00。一个 M 默认一个主会话顺序实施，相关测试随每个任务交付；跨会话从实际文件与最新合法 HEAD 接续。

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

## 接口与分期边界（v2复核）

本模块的 tool calling 能力探针使用静态工具 schema 与受控结果，验证模型协议而非 M06 的 MCP。真实 MCP 往返由 M06 负责，端到端调用由 M07/M08 负责，避免形成隐藏依赖环。网关计数包含底层 SDK 实际尝试，关闭/纳管 SDK 默认重试；缺少授权额度时离线实现可完成，live 单列 PENDING_LIVE。

## 本轮交付

按通用规划 Prompt 自适应决定任务粒度；可只给一份实施任务，需要拆分时才给最少必要的 S 和依赖。已有成果先验收/补差异，不重复建设。保留本模块全部关键失败案例和适用的分层验收。路径：`docs/MODULES/M03.md`、`handoffs/M03.md`、`reports/M03/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
