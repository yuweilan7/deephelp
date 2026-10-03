"""Authenticated loopback reader. It never opens trace files or invokes business tools."""

import argparse
import asyncio

import httpx

from deephelp_app.debug import export_json
from deephelp_app.errors import ConfigurationError
from deephelp_app.local_paths import local_path
from deephelp_app.mvp_runtime import LocalAuth, validate_control_paths


async def read(args: argparse.Namespace) -> None:
    auth = LocalAuth(local_path(args.auth))
    async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
        result = await client.get(
            f"http://127.0.0.1:{args.port}/debug/runs/{args.run_id}/{args.view}",
            headers={"Authorization": "Bearer " + auth.rows[0][0]},
            params={"session_id": args.session, "export": "true"},
        )
    if result.status_code != 200:
        raise ConfigurationError(f"Debug unavailable: HTTP {result.status_code}")
    content = export_json(result.json())
    if args.output:
        path = local_path(args.output)
        validate_control_paths([local_path(args.auth)], output=path)
        with path.open("x", encoding="utf-8") as file:
            file.write(content + "\n")
        print("Exported redacted debug view")
    else:
        print(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("view", choices=["intent", "turns", "flow"])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--auth", default=".local/m08/auth.json")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output", help="New .local JSON file; existing files are never replaced")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not args.run_id.replace("-", "").isalnum():
        parser.error("Invalid port or run identifier")
    try:
        asyncio.run(read(args))
    except (ConfigurationError, OSError, httpx.HTTPError) as exc:
        print(type(exc).__name__ + ": debug read failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
