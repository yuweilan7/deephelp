"""Bounded Linux sampling of the actual combined host; drive workloads separately."""

import argparse
import base64
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path


def command(*args):
    return subprocess.check_output(args, text=True, timeout=10).strip()


def values(path):
    return {k: int(v) for k, v in (line.split() for line in path.read_text().splitlines())}


def group(path):
    return {
        "memory": int((path / "memory.current").read_text()),
        "events": values(path / "memory.events"),
    }


def cpu():
    counts = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
    return sum(counts), counts[3] + counts[4]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace-id", required=True)
    args = parser.parse_args()
    root = Path("/srv/deephelp-langfuse")
    if not args.live or not 180 <= args.seconds <= 600 or root.resolve() != root:
        parser.error("Explicit live, canonical platform and 180..600 seconds required")
    if args.output.parent.resolve() != root / "config" or args.output.exists():
        parser.error("Use a new report inside platform config")
    # Exclusive reservation prevents concurrent writers from replacing evidence.
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    config = json.loads((root / "config/probe.json").read_text())
    auth = base64.b64encode((config["public_key"] + ":" + config["secret_key"]).encode()).decode()
    url = (
        config["host"]
        + "/api/public/v2/observations?traceId="
        + args.trace_id
        + "&fields=core,io,usage"
    )
    request = urllib.request.Request(url, headers={"Authorization": "Basic " + auth})
    ids = command("docker", "ps", "-q").splitlines()
    containers = json.loads(command("docker", "inspect", *ids))
    assert len(containers) >= 15, "Required combined containers absent"
    required = {
        "deephelp-mysql",
        "deephelp-redis",
        "deephelp-milvus",
        "deephelp-etcd",
        "deephelp-postgres-protocol",
    }
    required.update(
        "deephelp-observability-" + name + "-1"
        for name in ("collector", "tempo", "grafana", "prometheus")
    )
    required.update(
        "deephelp-langfuse-" + name + "-1"
        for name in ("web", "worker", "postgres", "redis", "clickhouse", "seaweedfs")
    )
    assert required <= {container["Name"].lstrip("/") for container in containers}
    services = ["deephelp-personal@" + name for name in ("app", "business", "retrieval")]
    groups = {
        unit: Path(
            "/sys/fs/cgroup" + command("systemctl", "show", unit, "-p", "ControlGroup", "--value")
        )
        for unit in services
    }
    for unit in services:
        assert command("systemctl", "is-active", unit) == "active"
    service_restarts = {
        unit: int(command("systemctl", "show", unit, "-p", "NRestarts", "--value"))
        for unit in services
    }
    start = time.monotonic()
    previous_cpu = cpu()
    initial_mem = dict(
        line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
    )
    initial_swap = int(initial_mem["SwapTotal"].split()[0]) - int(
        initial_mem["SwapFree"].split()[0]
    )
    initial_disk = shutil.disk_usage(root).used
    report = {
        "status": "PENDING",
        "samples": 0,
        "container_count": len(containers),
        "thresholds": {
            "available_kib": 1048576,
            "low_seconds": 10,
            "swap_growth_kib": 262144,
            "disk_free_bytes": 8 * 1024**3,
            "query_seconds": 5,
        },
        "initial_swap_kib": initial_swap,
        "available_min_kib": 2**63,
        "swap_growth_max_kib": 0,
        "simultaneous_cgroup_peak_bytes": 0,
        "observed_group_peak_bytes": {},
        "oom_kills": {},
        "cpu_percent_peak": 0,
        "cpu_percent_sum": 0,
        "query_ms_max": 0,
        "query_count": 0,
        "restart_deltas": {},
        "planned_worker_stop_samples": 0,
        "service_restart_deltas": {},
    }
    baseline_restart = {c["Name"]: c["RestartCount"] for c in containers}
    low_since = None
    try:
        while time.monotonic() - start < args.seconds:
            mem = dict(
                line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
            )
            available = int(mem["MemAvailable"].split()[0])
            swap = int(mem["SwapTotal"].split()[0]) - int(mem["SwapFree"].split()[0])
            report["available_min_kib"] = min(report["available_min_kib"], available)
            report["swap_growth_max_kib"] = max(report["swap_growth_max_kib"], swap - initial_swap)
            low_since = low_since or time.monotonic() if available < 1048576 else None
            assert low_since is None or time.monotonic() - low_since < 10, "MemAvailable threshold"
            assert swap - initial_swap <= 262144, "Swap growth threshold"
            assert shutil.disk_usage(root).free >= 8 * 1024**3, "Disk threshold"
            inspected = json.loads(command("docker", "inspect", *ids))
            active_groups = dict(groups)
            for container in inspected:
                name = container["Name"]
                report["restart_deltas"][name] = container["RestartCount"] - baseline_restart[name]
                assert not report["restart_deltas"][name] and not container["State"]["OOMKilled"], (
                    name
                )
                if not container["State"]["Running"]:
                    assert (
                        name == "/deephelp-langfuse-worker-1"
                        and (root / "config/capacity-worker-stopped").exists()
                    ), name
                    report["planned_worker_stop_samples"] += 1
                    continue
                active_groups[name] = Path(
                    "/sys/fs/cgroup/system.slice/docker-" + container["Id"] + ".scope"
                )
            simultaneous = 0
            for name, path in active_groups.items():
                observed = group(path)
                simultaneous += observed["memory"]
                report["observed_group_peak_bytes"][name] = max(
                    report["observed_group_peak_bytes"].get(name, 0), observed["memory"]
                )
                report["oom_kills"][name] = max(
                    report["oom_kills"].get(name, 0), observed["events"]["oom_kill"]
                )
                assert not report["oom_kills"][name], name + " OOM"
            report["simultaneous_cgroup_peak_bytes"] = max(
                report["simultaneous_cgroup_peak_bytes"], simultaneous
            )
            current_cpu = cpu()
            total, idle = (a - b for a, b in zip(current_cpu, previous_cpu, strict=True))
            usage = 100 * (1 - idle / total) if total else 0
            previous_cpu = current_cpu
            report["cpu_percent_peak"] = max(report["cpu_percent_peak"], usage)
            report["cpu_percent_sum"] += usage
            if report["samples"] % 5 == 0:
                for unit in services:
                    assert command("systemctl", "is-active", unit) == "active"
                    delta = (
                        int(command("systemctl", "show", unit, "-p", "NRestarts", "--value"))
                        - service_restarts[unit]
                    )
                    report["service_restart_deltas"][unit] = delta
                    assert delta == 0, unit + " restarted"
                query_start = time.monotonic()
                with urllib.request.urlopen(request, timeout=5) as response:
                    rows = json.load(response)["data"]
                assert len(rows) == 2 and any(
                    row["output"] == "Synthetic amount 173.45" for row in rows
                )
                latency = (time.monotonic() - query_start) * 1000
                assert latency < 5000, "Query latency threshold"
                report["query_ms_max"] = max(report["query_ms_max"], latency)
                report["query_count"] += 1
            report["samples"] += 1
            time.sleep(1)
        report["status"] = "PASS"
    except BaseException as error:
        report.update(status="FAIL", error_type=type(error).__name__, reason=str(error))
        raise
    finally:
        report["window_seconds"] = time.monotonic() - start
        report["cpu_percent_mean"] = report.pop("cpu_percent_sum") / max(report["samples"], 1)
        report["disk_growth_bytes"] = shutil.disk_usage(root).used - initial_disk
        report["disk_free_bytes"] = shutil.disk_usage(root).free
        args.output.write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    key: value
                    for key, value in report.items()
                    if key not in ("observed_group_peak_bytes", "oom_kills", "restart_deltas")
                }
            )
        )


if __name__ == "__main__":
    main()
