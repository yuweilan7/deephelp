# M01：一页异步说明

`async def` 定义可暂停的协程；调用它先得到协程对象，`await` 才推进执行。等待真正的异步 I/O 时，当前任务把事件循环交给其它任务。`await` 不等于另起线程：在协程里直接跑同步 CPU 循环，仍会堵住同一事件循环线程。

| Python 做法 | Java 类比与边界 |
|---|---|
| `await` 异步 HTTP | 类似异步结果的等待/续接；不能照搬阻塞式 `Future.get()` 到事件循环 |
| `TaskGroup.create_task` | 类似协调多个 `CompletableFuture`；TaskGroup 任一子任务异常会取消其它任务并等待其收尾，`allOf` 本身不保证取消兄弟任务 |
| `Semaphore(2)` | 类似 Java 信号量限制同时进入上游的调用；不是新线程池，也不代表 QPS 配额 |
| `asyncio.timeout` + `finally` | 类似超时控制加资源释放；取消是协作式的，不等于强制杀线程 |
| `asyncio.to_thread` | 类似交给线程池，适合短同步文件 I/O/阻塞 SDK；普通 CPython 线程受 GIL 影响，纯 Python CPU 工作不保证提速 |
| `ProcessPoolExecutor` | 独立进程执行 CPU 工作，可绕过同一解释器的 GIL；参数/返回值须可序列化，有启动与内存成本 |

从仓库根执行 `uv run --locked python -m deephelp_app.experiments`，能在 Python 3.14.7 下运行三个实验。它们不访问网络、不使用 API key。

1. **独立 I/O：** 两次合成等待先串行再由 TaskGroup 并发。返回 A/B 结果、并发峰值和两种耗时。串行峰值为 1、并发为 2；通常串行耗时接近两段等待之和，并发接近较长一段。测试验证任务重叠，具体耗时仅供观察。
2. **失败和超时：** 一个子任务等待另一个已启动，再抛异常或耗尽自身 timeout。TaskGroup 取消等待中的兄弟任务；兄弟记录取消、执行 `finally` 清理，组在全部任务收尾后抛异常组。实验只记录预期失败；生产代码必须映射成明确错误，不能当成功。
3. **CPU 阻塞：** 同步 CPU 循环直接运行时 heartbeat 完全停住；把同一工作交给进程池并 `await` 时 heartbeat 继续。Windows 进程池入口必须放在 `if __name__ == '__main__'` 保护下。运行中的线程/进程工作不能靠取消 await 强制终止；池的等待关闭移到线程，避免在事件循环里同步等待工作结束。

请求层固定一个总 deadline，所有后续调用传同一预算对象。Semaphore 排队、子调用和重试都计入这个 deadline；每层不能重新创建预算。子 timeout 给某一次调用上限，有限只读重试同时扣剩余调用数与全请求重试数。`CancelledError` 继续传播，资源放在 `async with/finally` 中清理；遇到副作用结果未知，不套用这里的只读重试机制。

应用启动只创建一个共享 HTTP 连接池，正常退出统一关闭。领域 DTO 只含可 JSON 序列化数据；客户端、锁、Semaphore、预算时钟等留在运行时资源中。后续分词/训练可替换进程执行位置，本模块不引入任务队列。

实现依据：[Python 3.14 任务/取消与线程说明](https://docs.python.org/3.14/library/asyncio-task.html)、[HTTPX transports 与 ASGI lifespan 边界](https://www.python-httpx.org/advanced/transports/)、[FastAPI lifespan 资源管理](https://fastapi.tiangolo.com/advanced/events/)。
