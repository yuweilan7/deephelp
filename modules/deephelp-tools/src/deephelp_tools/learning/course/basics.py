"""实际领域校验与Python写法实验；不访问模型。"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import ValidationError

from deephelp_app.domain.models.common import Money, VerifiedIdentity


async def types_demo() -> dict[str, object]:
    @dataclass
    class Notes:
        # default_factory每个实例各建一份list，避免两个请求共用可变容器。
        events: list[str] = field(default_factory=list)

    first, second = Notes(), Notes()
    first.events.append("accepted")
    assert not second.events
    money = Money(amount=Decimal("99.90"))
    failures: list[str] = []
    for value in (99.90, "99.901", "NaN"):
        try:
            Money.model_validate({"amount": value})
        except ValidationError:
            failures.append(str(value))
    assert len(failures) == 3
    identity = VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a")
    try:
        identity.user_id = "other"
    except ValidationError:
        failures.append("frozen_identity")
    assert failures[-1] == "frozen_identity"
    return {"money": money.model_dump(mode="json"), "rejected": failures, "isolated_lists": True}


async def syntax_demo() -> dict[str, object]:
    events: list[str] = []

    def rows() -> Iterator[tuple[str, str]]:
        events.append("generator_started")
        yield "order_id", "000031"
        yield "coupon_id", "000009"

    generator = rows()
    assert not events  # 生成器调用只创建对象，next/for才进入函数体。
    slots = {name: value for name, value in generator}
    assert events == ["generator_started"]
    copied = {**slots, "order_id": "000042"}
    assert slots["order_id"] == "000031" and copied["order_id"] == "000042"

    def prefixer(prefix: str) -> Callable[[str], str]:
        # 闭包保存外层变量；此处没有线程，也没有全局可变状态。
        def label(value: str) -> str:
            return prefix + value

        return label

    @contextmanager
    def resource() -> Iterator[str]:
        events.append("opened")
        try:
            yield "synthetic_resource"
        finally:
            events.append("closed")

    with resource() as owned:
        events.append(prefixer("using:")(owned))
    assert events[-1] == "closed"
    return {"slots": slots, "copy": copied, "events": events}
