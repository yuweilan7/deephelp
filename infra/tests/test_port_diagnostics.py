"""公共端口诊断的离线反例：已探测PG端口不能索引旧MySQL端口，泄漏/无SSH不能报成功。

脚本运行即联网，因此以受控socket与合成settings运行原入口；报告只写测试拥有的临时目录。
这组测试不证明真实防火墙或云端监听状态。
"""

import io
import json
import runpy
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "client/doctor.py"


class PortDiagnosticsTests(unittest.TestCase):
    def run_diagnostics(self, root, responses):
        settings = types.ModuleType("settings")
        settings.BASE = root
        settings.settings = lambda: {"DEEPHELP_SSH_HOST": "synthetic.invalid"}

        class Connection:
            def __init__(self, address):
                self.port = address[1]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def settimeout(self, _):
                pass

            def sendall(self, _):
                pass

            def recv(self, _):
                return responses.get(self.port, b"")

        output = io.StringIO()
        with (
            patch.dict("sys.modules", {"settings": settings}),
            patch("socket.create_connection", side_effect=lambda address, **_: Connection(address)),
            redirect_stdout(output),
        ):
            runpy.run_path(str(SCRIPT))
        return output.getvalue()

    def test_quiet_database_ports_produce_report_without_old_port_key_error(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            output = self.run_diagnostics(root, {22: b"SSH-2.0-synthetic\r\n"})
            report = json.loads((root / "reports/public-port-check.json").read_text())
            self.assertIn("PUBLIC_ACCESS_CHECK PASS", output)
            self.assertIn("15432", report["probe"])
            self.assertNotIn("3306", report["probe"])

    def test_any_checked_database_protocol_response_prevents_success_report(self):
        for port in (15432, 6379, 19530, 9091):
            with self.subTest(port=port), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "reports").mkdir()
                with self.assertRaises(AssertionError):
                    self.run_diagnostics(root, {22: b"SSH-2.0-synthetic\r\n", port: b"LEAK"})
                self.assertFalse((root / "reports/public-port-check.json").exists())

    def test_missing_ssh_banner_prevents_success_report(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            with self.assertRaises(AssertionError):
                self.run_diagnostics(root, {})
            self.assertFalse((root / "reports/public-port-check.json").exists())


if __name__ == "__main__":
    unittest.main()
