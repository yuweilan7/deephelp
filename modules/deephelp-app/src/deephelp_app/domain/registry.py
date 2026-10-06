"""显式版本化意图目录，实体、600与SOP共用同一层级和槽位定义。

层级来自parent_code与label_path，不能靠编号拼写猜父子。
complaint_registry按需构造，导入模块不读取外部文件或服务。
"""

from typing import Literal, cast

from pydantic import model_validator

from deephelp_app.domain.models.common import DTO, REGISTRY_VERSION
from deephelp_app.domain.models.entities import EntityName
from deephelp_app.domain.models.intents import IntentCode, IntentDefinition


class IntentRegistry(DTO):
    version: str
    intents: tuple[IntentDefinition, ...]

    @model_validator(mode="after")
    def hierarchy(self) -> IntentRegistry:
        published = catalog_codes(self.version)
        entries = {item.code: item for item in self.intents}
        if len(entries) != len(self.intents) or set(entries) != set(published):
            raise ValueError("Registry must contain every published code exactly once")
        for item in self.intents:
            if item.version != self.version:
                raise ValueError("Entry and catalog versions differ")
            levels = tuple(x for x in (item.l1, item.l2, item.l3, item.l4) if x is not None)
            if item.parent_code is None:
                if len(levels) != 1:
                    raise ValueError("Root must have exactly one level")
            else:
                parent = entries.get(item.parent_code)
                if parent is None:
                    raise ValueError("Parent is outside pinned catalog")
                parent_levels = tuple(
                    x for x in (parent.l1, parent.l2, parent.l3, parent.l4) if x is not None
                )
                if levels[:-1] != parent_levels or parent.is_actionable:
                    raise ValueError("Child must extend its explicit non-actionable parent")
        return self

    def get(self, code: IntentCode) -> IntentDefinition:
        return next(item for item in self.intents if item.code == code)

    @property
    def actionable_codes(self) -> tuple[IntentCode, ...]:
        return tuple(item.code for item in self.intents if item.is_actionable)


def catalog_codes(version: str) -> tuple[IntentCode, ...]:
    """固定版本的闭包；新枚举不会自动成为旧发布可执行的标签。

    v1保留原父级和三叶子，v2只增加已实现活动资格叶子。未知版本拒绝。
    """
    if version not in {"complaints-v1", "complaints-v2", "complaints-v3"}:
        raise ValueError("Unsupported registry version")
    return tuple(
        c
        for c in IntentCode
        if (
            version == "complaints-v3"
            or c not in {IntentCode.ORDER_CREATION_FAILED, IntentCode.REFUND_COUPON_NOT_RETURNED}
        )
        and (version != "complaints-v1" or c != IntentCode.ACTIVITY_ELIGIBILITY_QUERY)
    )


def complaint_registry(version: str = REGISTRY_VERSION) -> IntentRegistry:
    """Construct on demand; importing the package performs no file or service I/O."""
    legacy = IntentRegistry(
        version=REGISTRY_VERSION,
        intents=(
            IntentDefinition(
                code=IntentCode.SERVICE, label="合成客诉服务", l1="service", is_actionable=False
            ),
            IntentDefinition(
                code=IntentCode.BENEFIT_ISSUE,
                label="优惠问题",
                l1="service",
                l2="benefit",
                parent_code=IntentCode.SERVICE,
                is_actionable=False,
            ),
            IntentDefinition(
                code=IntentCode.ORDER_QUERY,
                label="订单查询",
                l1="service",
                l2="order",
                parent_code=IntentCode.SERVICE,
                is_actionable=False,
            ),
            IntentDefinition(
                code=IntentCode.DISCOUNT_MISSING,
                label="优惠未享受",
                l1="service",
                l2="benefit",
                l3="discount_missing",
                parent_code=IntentCode.BENEFIT_ISSUE,
                is_actionable=True,
                required_slots=(EntityName.ORDER_ID,),
                sop_id="discount-missing-v1",
            ),
            IntentDefinition(
                code=IntentCode.COUPON_UNUSABLE,
                label="券不可用",
                l1="service",
                l2="benefit",
                l3="coupon_unusable",
                parent_code=IntentCode.BENEFIT_ISSUE,
                is_actionable=True,
                required_slots=(EntityName.ORDER_ID, EntityName.COUPON_ID),
                sop_id="coupon-unusable-v1",
            ),
            IntentDefinition(
                code=IntentCode.ORDER_ACTIVITY_QUERY,
                label="订单活动查询",
                l1="service",
                l2="order",
                l3="activity_query",
                parent_code=IntentCode.ORDER_QUERY,
                is_actionable=True,
                required_slots=(EntityName.ORDER_ID,),
                sop_id="order-activity-v1",
            ),
        ),
    )
    if version == REGISTRY_VERSION:
        return legacy
    catalog_codes(version)
    version = cast(Literal["complaints-v1", "complaints-v2", "complaints-v3"], version)
    return IntentRegistry(
        version=version,
        intents=(
            *(i.model_copy(update={"version": version}) for i in legacy.intents),
            IntentDefinition(
                code=IntentCode.ACTIVITY_ELIGIBILITY_QUERY,
                label="活动参与资格与名额查询",
                l1="service",
                l2="order",
                l3="activity_eligibility",
                parent_code=IntentCode.ORDER_QUERY,
                is_actionable=True,
                required_slots=(EntityName.ACTIVITY_ID,),
                sop_id="activity-eligibility-v1",
                version=version,
            ),
            *(
                (
                    IntentDefinition(
                        code=IntentCode.ORDER_CREATION_FAILED,
                        label="订单创建失败",
                        l1="service",
                        l2="order",
                        l3="creation_failed",
                        parent_code=IntentCode.ORDER_QUERY,
                        is_actionable=True,
                        required_slots=(EntityName.ATTEMPT_ID,),
                        sop_id="order-creation-failed-v1",
                        version=version,
                    ),
                    IntentDefinition(
                        code=IntentCode.REFUND_COUPON_NOT_RETURNED,
                        label="退款后券未退回",
                        l1="service",
                        l2="benefit",
                        l3="refund_coupon_return",
                        parent_code=IntentCode.BENEFIT_ISSUE,
                        is_actionable=True,
                        required_slots=(EntityName.REFUND_ID, EntityName.COUPON_ID),
                        sop_id="refund-coupon-return-v1",
                        version=version,
                    ),
                )
                if version == "complaints-v3"
                else ()
            ),
        ),
    )
