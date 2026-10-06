"""显式SSH容量观察，供索引导入与飞轮候选准备调用。

沿原known_hosts与连接配置只读观察专用宿主的磁盘、Milvus健康和内存；不部署或重启。
当前调用拥有SSH子进程，失败/超时/取消在finally终止并回收；结果只用于本次容量准入。
业务Milvus/Redis模块不再导入这个运维入口。
"""

import asyncio
import json
import os
from pathlib import Path

from deephelp_app.application.dense import Capacity
from deephelp_app.bootstrap.connections import local_connection
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode


class SSHCapacity:
    """Read-only checks against the existing P00 host; no deployment or restart command."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = local_connection(root) if os.name == "nt" else None
        self.observations: list[dict[str, int]] = []

    async def __call__(self) -> Capacity:
        remote = (
            "sudo python3 -c 'import json,shutil,subprocess; "
            'd=json.loads(subprocess.check_output(["docker","inspect","deephelp-milvus"]))[0]; '
            's=json.loads(subprocess.check_output(["docker","stats","--no-stream",'
            '"--format","{{json .}}","deephelp-milvus"])) ; '
            'units={"B":1,"KiB":1024,"MiB":1048576,"GiB":1073741824}; '
            'v=s["MemUsage"].split("/")[0].strip(); '
            'u=next(k for k in ("GiB","MiB","KiB","B") if v.endswith(k)); '
            'a=int(next(l.split()[1] for l in open("/proc/meminfo") '
            'if l.startswith("MemAvailable:")))*1024; '
            'print(json.dumps({"root_free":shutil.disk_usage("/").free,'
            '"milvus_data":int(subprocess.check_output(["du","-s","-B1","/srv/deephelp-data/milvus"]).split()[0]),'
            '"memory_used":int(float(v[:-len(u)])*units[u]),"memory_limit":d["HostConfig"]["Memory"],'
            '"host_available":a,"healthy":d["State"]["Health"]["Status"]=="healthy","oom":d["State"]["OOMKilled"]}))'
            + "'"
        )
        d = self.config
        # 创建真正的SSH子进程，参数按数组传入而非拼本地shell命令。
        # communicate等待输出/退出，外层30秒限定总等待；stderr不向公开诊断回显。
        # 云安装在已核定的专用宿主本地读同一观察，不引用Windows私钥或另开SSH跳板。
        # 两条传输共用同一脚本/健康判断；本次调用仍拥有子进程取消与回收。
        command = ["sudo", "python3", "-c", remote[len("sudo python3 -c '") : -1]]
        if d is not None:
            command = [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "UserKnownHostsFile=" + str(self.root / "infra/client/known_hosts"),
                "-o",
                "ConnectTimeout=12",
                "-i",
                d["DEEPHELP_SSH_KEY_WINDOWS"],
                "-p",
                d["DEEPHELP_SSH_PORT"],
                d["DEEPHELP_SSH_USER"] + "@" + d["DEEPHELP_SSH_HOST"],
                remote,
            ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(30):
                stdout, _ = await process.communicate()
            if process.returncode:
                raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Read-only capacity probe failed")
            data = json.loads(stdout)
            if not data.pop("healthy") or data.pop("oom"):
                raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Milvus unhealthy or OOM detected")
            capacity = Capacity(**data)
            self.observations.append(data)
            del self.observations[:-32]
            return capacity
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()
