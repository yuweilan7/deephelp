"""反馈去重与冻结数据审计共用的编号中立文本指纹。

canonical去掉样本编号差异以发现近似重复，不能用于改写真实用户原文或授权。
"""

import re
import unicodedata


def canonical(text: str) -> str:
    # Also audit identifier-only rewrites: changing an order is not an independent case.
    value = unicodedata.normalize("NFKC", text).lower()
    value = re.sub(r"(?:demo|coupon|sku|activity)[-_a-z0-9]+|[0-9]+", "#", value)
    return re.sub(r"[\s，。；、：,.!?！？]+", "", value)
