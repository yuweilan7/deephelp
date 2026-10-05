# 02｜DTO、dataclass、Pydantic与金额

[上一章](01-start.md) · [课程入口](README.md) · [下一章](03-python-patterns.md)

## 业务问题与目标

订单号保留前导零，金额不能静默舍入，身份不能被下游改写。目标是分清类型提示与实际校验，理解值对象为什么不持有连接。前置是类、列表和字符串。

## 普通话解释

`value: str`告诉读者和mypy预期类型，Python本身不会因此拒绝整数。Pydantic构造器/model_validate在运行时检查；DTO的extra=forbid拒绝多余字段、frozen=True拒绝字段赋值。dataclass生成初始化/比较等方法，不自动提供Pydantic输入规则。

Java record接近不可变值的意图，但frozen不递归冻结内部list/dict。更新常用model_copy(update=...)生成新值；该方法默认不校验update，安全边界需要model_validate(copy.model_dump())重新校验。copy不是校验器。

field(default_factory=list)每次创建实例都调用list，得到独立容器。可变请求统计用dataclass；可序列化身份、金额和预算快照用DTO。客户端、事件循环、锁和连接只存在运行对象中。

## 实际实验

离线、合成、模型0次：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course types
```

预期money.amount为字符串99.90、currency为CNY；rejected含float、三位小数、NaN、frozen_identity；isolated_lists为true。按[types_demo](../../modules/deephelp-tools/src/deephelp_tools/learning/course/basics.py)逐步读：

1. first/second各建Notes；追加first.events，second仍空，说明容器所有权。
2. Decimal("99.90")从十进制文本构造；Decimal(99.90)会带入float的二进制误差。
3. BeforeValidator先拒float，Field拒负数/非有限值，precision拒超过两位，不自动round。
4. 构造VerifiedIdentity后修改user_id触发ValidationError，实验捕获预期反例。

## 当前源码

读[common.DTO/Money/VerifiedIdentity](../../modules/deephelp-app/src/deephelp_app/domain/models/common.py)、[RequestEnvelope](../../modules/deephelp-app/src/deephelp_app/domain/models/conversation.py)。Annotated把str与Field元数据放在一起；校验在Pydantic消费时执行。StrEnum可参与字符串协议，内部比较用明确枚举成员。

[ports.ChatPort](../../modules/deephelp-app/src/deephelp_app/domain/ports.py)是Protocol，实现有匹配chat签名即可，不必继承。类似Java接口的依赖倒置，但Python结构类型默认没有JVM式运行期强制声明。tuple[Fact, ...]表示任意长度元组，X | None使用前仍要判断。

[ResponseEnvelope.closeout](../../modules/deephelp-app/src/deephelp_app/domain/models/responses.py)校验跨字段规则：ANSWERED必须有事实且不缺槽，每条事实要有证据，WAITING_SLOT对应CLARIFY。单字段类型正确不代表整份业务值正确。

## 常见误区与反例

- order_id=31丢失000031；标识符不是算术数字。
- frozen里嵌套list仍可修改；不要跨请求共用默认list。
- 注解不代替鉴权、归属和版本规则；这些仍由用例/数据库守卫。

## 练习与答案

预测Money(amount="0.00")和Money(amount="-0.01")，能否把负数改成0继续回答？

答案：0.00合法，负数被ge=0拒绝。静默改0伪造事实，应报告明确错误。类型验证不证明模型能准确抽取金额，真实抽取要另有内容证据。
