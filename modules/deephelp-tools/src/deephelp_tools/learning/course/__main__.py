"""python -m deephelp_tools.learning.course <topic>：安装后同样可运行。"""

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable

from .async_flow import awaitables_demo, coordination_demo
from .basics import syntax_demo, types_demo
from .cancellation import cancellation_demo
from .offline import offline_only
from .request import request_demo
from .resources import resources_demo

TOPICS: dict[str, Callable[[], Awaitable[dict[str, object]]]] = {
    "types": types_demo,
    "syntax": syntax_demo,
    "request": request_demo,
    "awaitables": awaitables_demo,
    "coordination": coordination_demo,
    "cancellation": cancellation_demo,
    "resources": resources_demo,
}


async def run_topic(topic: str) -> dict[str, object]:
    # 每次CLI只运行明确选中的固定实验；超时不会转到live或重试其他模型。
    # Windows事件循环初始化要建内部socketpair，先建loop再封住业务网络。
    with offline_only():
        async with asyncio.timeout(30):
            return await TOPICS[topic]()


def main() -> None:
    parser = argparse.ArgumentParser(description="Java读者课程，离线合成实验，远程模型0次")
    parser.add_argument("topic", choices=TOPICS)
    args = parser.parse_args()
    result = asyncio.run(run_topic(args.topic))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
