# M01｜最小异步能力、工程骨架与测试底座

**阶段：** A 基础

**前置：** M00

**来源：** 原 PDF 文件页 18–24、69–71。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 本模块目标
用最少必要的新知识搭出后续模块能直接落代码的骨架。重点是 API 调用、超时取消、状态类型与可测性，不是先把 Python 生态全部学完。

## 具体范围
Python 3.12，保留既有 uv workspace 和单一根锁文件，新增业务包自己的 pyproject；FastAPI/Pydantic、pytest、静态检查工具和异步 HTTP 客户端按锁定版本选最小组合。应用启动/关闭统一管理客户端，不在每次请求创建连接池。开发、测试、live 调用分离；默认测试无外网且不消耗真实额度。密钥只从受控环境加载，启动日志不能打印完整设置对象。

实现一个 health API、一个有类型的占位 converse API、错误封装、request_id/trace_id 注入、JSONL 结构化 trace 接口，以及 fake ModelGateway/Repository。此时不能假装 Agent 已实现。约定日志事件只有输入摘要、决策依据、候选、工具参数和结果摘要，不要求模型披露隐藏思维链。

把 async 学习压缩成三个可运行实验：两个独立 I/O 并发与串行耗时差；一个子调用超时/失败时其它任务如何取消并收尾；同步 CPU 重任务阻塞事件循环的对照与安全转移。解释 async/await、TaskGroup 或等价并发、Semaphore、timeout/cancellation、线程与进程边界，并用 Java CompletableFuture/线程池作类比，但说明 await 不等于另起线程。

资源策略：同一请求总 deadline、子调用各自 timeout、客户端连接上限、LLM 并发默认 1–2、所有层共享一次请求的剩余重试预算。CPU 分词/训练不能直接在异步请求主循环里跑很久；本轮只给可替换执行位置，不引入 Celery/RabbitMQ。

## 骨架不变量
领域 DTO 不包含 SDK client、数据库连接、锁对象；将来要进入 checkpoint 的状态必须能序列化。依赖倒置在真实 Port 上即可。测试目录明确 unit/integration/live/e2e；live 必须显式开关且受调用预算限制。单模块导入不应自动连数据库或调用模型。

## 必测案例
取消请求后 HTTP 连接与任务均被释放；异常不会被 except Exception 吞掉成假成功；两个请求 trace 不串；缺密钥时 fake 测试可运行、live 清楚报配置错误；超时重试次数有断言；server 正常退出时池被关闭；同步阻塞示例确实能被测试或测量识别。

交付准确安装/检查/测试命令、目录骨架、最小 CI、异步练习及一页解释。无需安装 AgentScope、GPU 依赖、前端构建链或全栈观测平台。不要为了一个 health API 写十层抽象。

## 接口与分期边界

只创建符合 M00 语义的最小类型子集和 fake 适配；标注占位接口 NOT_IMPLEMENTED。M02 在这些定义上补全，不另起第二套 Request/Response。异步实验优先事件/任务完成状态等确定性断言；耗时对照给宽容阈值与环境，不以毫秒级时序作脆弱 CI 门禁。新增依赖与根锁由本模块主写者统一更新，uv 不在 PATH 时用已安装的 `py -3.11 -m uv`，不重装工程。
