# 03｜在当前行读懂Python高级写法

[上一章](02-values.md) · [课程入口](README.md) · [请求主线](08-message-path.md)

## 业务问题与目标

推导式、解包、闭包、装饰器和上下文管理器容易掩盖调用顺序。目标是还原成普通循环，说明何时执行、谁清理。前置为for、函数、try/finally和第02章。

## 最小实验与逐步解释

离线、合成、模型0次：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course syntax
```

预期slots.order_id=000031、copy.order_id=000042；events为generator_started、opened、using:synthetic_resource、closed。读[syntax_demo](../../modules/deephelp-tools/src/deephelp_tools/learning/course/basics.py)。

rows()只创建生成器，首次迭代才进入函数；yield把一对值交给调用者并暂停，下次从原位置继续。Java Stream的延迟执行能帮助理解，但生成器保存函数执行位置，消费完后不能当集合反复遍历。

字典推导式可展开：

```python
slots = {}
for pair in generator:
    name, value = pair
    slots[name] = value
```

name/value是元组解包，元素数必须匹配。`{**slots, "order_id": "000042"}`新建字典并覆盖键；嵌套list不会自动深复制。定义位置*args/**kwargs收集参数，调用位置则展开参数。

prefixer返回label，label使用外层prefix，叫闭包。Java lambda捕获可类比，但Python通常捕获变量绑定，循环里延迟lambda可能全看到最后的值；需要固定值时用明确参数或工厂函数。

@contextmanager在定义时包装resource；进入with运行到yield，把值交给owned，退出继续yield后并执行finally。with块抛错仍清理，异常仍向外传播。普通返回对象不提供这个退出协议。

## 生产源码中的对应写法

[Conversation](../../modules/deephelp-app/src/deephelp_app/application/conversation/orchestrator.py)的stage=stage把职责函数放入类，实例调用自动传self，仍只有一个引擎。@observed包装入口采集实际观测，不把函数主体提前运行。[RunTrace](../../modules/deephelp-app/src/deephelp_app/application/conversation/tracking.py)用dataclass/default_factory保存本次计数。

Annotated携带校验元数据，Final给静态检查器表达不重绑定意图，不是运行期Java final字段保护。Callable[[str], str]描述函数签名，Iterator[tuple[str, str]]描述yield形状；都不启动任务或资源。

async with的进入/退出可await；async for等待下一个异步值。两次连续await仍顺序；await也不保证每次都切换任务，已完成结果可马上返回。

## 常见误区与反例

- 涉及权限/网络/事务的过长推导式难调试，优先普通循环和命名。
- with只关闭自己拥有的资源，借用共享client不该由请求关闭。
- 中途退出for不保证马上执行生成器清理；资源要有明确with边界。

## 练习与答案

在with块抛ValueError，预测events末尾；复制字典后修改嵌套list会怎样？

答案：finally追加closed，ValueError继续传播，清理成功不等于业务成功。解包为浅复制，嵌套list可能共享，应用不可变值或在所有权边界显式复制；不盲目深复制客户端和锁。
