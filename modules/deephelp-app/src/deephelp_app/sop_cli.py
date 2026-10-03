"""Offline registry validation, immutable publication and content-verified rollback."""

import argparse
import json
from pathlib import Path

from deephelp_app.live_probe import local_path
from deephelp_app.sop_governance import RegistryStore, SOPRegistry, bundled_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="M14 SOP registry governance (offline)")
    parser.add_argument("command", choices=["validate", "publish", "show", "rollback"])
    parser.add_argument("--source", type=Path, help="Omit to use the bundled synthetic registry")
    parser.add_argument("--directory", default=".local/m14/registry")
    args = parser.parse_args()
    try:
        store = RegistryStore(local_path(args.directory))
        if args.command in {"validate", "publish"}:
            registry = (
                SOPRegistry.model_validate_json(args.source.read_text(encoding="utf-8"))
                if args.source
                else bundled_registry()
            )
            key = store.publish(registry) if args.command == "publish" else registry.snapshot_hash
            result = {
                "snapshot": key,
                "registry_version": registry.registry_version,
                "sops": {d.intent_code.value: d.version for d in registry.definitions},
            }
        elif args.command == "rollback":
            result = {"snapshot": store.rollback()}
        else:
            history = store.history()
            if not history:
                raise ValueError("No published registry")
            result = {
                "active": history[0].snapshot_hash,
                "retained": [s.snapshot_hash for s in history],
            }
        print(json.dumps({"status": "PASS", **result}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
