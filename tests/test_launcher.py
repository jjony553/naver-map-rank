"""실제 pip 설치나 브라우저 열기 없이 실행기의 설치·접속 흐름을 검증합니다."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.requirements = self.root / "requirements.txt"
        self.requirements.write_text("Flask>=3.1,<4\nplaywright>=1.56,<2\n", encoding="utf-8")
        self.venv = self.root / ".venv"
        self.python = self.venv / "Scripts" / "python.exe"
        self.state_file = self.root / ".setup-state.json"
        self.paths = patch.multiple(launcher, ROOT=self.root, VENV=self.venv,
                                    PYTHON=self.python, STATE_FILE=self.state_file)
        self.paths.start()
        self.print_patch = patch("builtins.print")
        self.print_patch.start()

    def tearDown(self):
        self.print_patch.stop()
        self.paths.stop()
        self.temp.cleanup()

    def make_python(self):
        self.python.parent.mkdir(parents=True)
        self.python.touch()

    def fingerprint(self):
        return hashlib.sha256(self.requirements.read_bytes()).hexdigest()

    @staticmethod
    def completed(command, **_kwargs):
        output = json.dumps({"collectorReady": True}) if "collector_health" in command[-1] else ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    def test_first_install_creates_dedicated_venv_and_never_uses_global_pip(self):
        with patch.object(launcher.subprocess, "run", side_effect=self.completed) as process:
            launcher.prepare()
        commands = [call.args[0] for call in process.call_args_list]
        self.assertEqual(commands[0], [sys.executable, "-m", "venv", str(self.venv)])
        pip_commands = [command for command in commands if "pip" in command]
        self.assertEqual(len(pip_commands), 1)
        self.assertEqual(pip_commands[0][0], str(self.python))
        self.assertIn(str(self.requirements), pip_commands[0])
        for call in process.call_args_list:
            self.assertEqual(call.kwargs["cwd"], self.root)
            if "pip" in call.args[0]:
                self.assertEqual(call.kwargs["env"]["PIP_CACHE_DIR"], str(self.root / ".pip-cache"))
        self.assertEqual(json.loads(self.state_file.read_text())["requirements"], self.fingerprint())

    def test_matching_requirements_skip_install_only_when_import_probe_passes(self):
        self.make_python()
        for marker, probe_ok, installs in [
            ({"requirements": self.fingerprint()}, True, False),
            ({"requirements": self.fingerprint()}, False, True),
            ([], True, True),
        ]:
            with self.subTest(marker=marker, probe_ok=probe_ok):
                self.state_file.write_text(json.dumps(marker), encoding="utf-8")

                def result(command, **kwargs):
                    if command[-1] == "import flask; import playwright.sync_api":
                        return subprocess.CompletedProcess(command, 0 if probe_ok else 1, stdout="", stderr="")
                    return self.completed(command, **kwargs)

                with patch.object(launcher.subprocess, "run", side_effect=result) as process:
                    launcher.prepare()
                pip_calls = [call for call in process.call_args_list if "pip" in call.args[0]]
                self.assertEqual(bool(pip_calls), installs)
                self.assertTrue(any(call.args[0][-1] == "import flask; import playwright.sync_api"
                                    for call in process.call_args_list))

    def test_failed_pip_does_not_record_success_or_continue_to_browser_install(self):
        self.make_python()

        def fail_pip(command, **kwargs):
            if "pip" in command:
                return subprocess.CompletedProcess(command, 1)
            return self.completed(command, **kwargs)

        with patch.object(launcher.subprocess, "run", side_effect=fail_pip) as process:
            with self.assertRaisesRegex(RuntimeError, "설치를 완료하지 못"):
                launcher.prepare()
        self.assertFalse(self.state_file.exists())
        self.assertFalse(any("collector_health" in call.args[0][-1] for call in process.call_args_list))

    def test_existing_app_checks_python_identity_and_never_uses_environment_proxy(self):
        cases = [
            ({"service": "place-rank-python", "runtime": "python"}, True),
            ({"service": "place-rank-local", "runtime": "node"}, False),
            ({"service": "other-program", "runtime": "python"}, False),
            ({"ok": True}, False),
            ([], False),
            (None, False),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                opener = Mock()
                opener.open.return_value = io.BytesIO(json.dumps(value).encode())
                with patch.object(launcher.urllib.request, "ProxyHandler", wraps=launcher.urllib.request.ProxyHandler) as proxy:
                    with patch.object(launcher.urllib.request, "build_opener", return_value=opener):
                        self.assertEqual(launcher.existing_app(4173), expected)
                proxy.assert_called_once_with({})
                self.assertEqual(opener.open.call_args.args[0].full_url, "http://127.0.0.1:4173/api/health")
                self.assertEqual(opener.open.call_args.kwargs["timeout"], 1)

    def test_busy_port_has_actionable_error_and_does_not_open_browser(self):
        with patch.object(launcher, "existing_app", return_value=False):
            with patch("werkzeug.serving.make_server", side_effect=OSError("address already in use")):
                with patch.object(launcher.webbrowser, "open") as browser:
                    with self.assertRaisesRegex(RuntimeError, "4173.*다른 프로그램.*4174"):
                        launcher.serve(4173, open_browser=True)
                    browser.assert_not_called()

    def test_bootstrap_restarts_with_venv_python_and_preserves_no_browser_option(self):
        with patch.object(launcher, "prepare") as prepare:
            with patch.object(launcher.subprocess, "call", return_value=7) as child:
                with patch.object(launcher.sys, "argv", ["launcher.py", "--port", "4174", "--no-browser"]):
                    with patch.object(launcher.webbrowser, "open") as browser:
                        result = launcher.main()
        self.assertEqual(result, 7)
        prepare.assert_called_once_with()
        command = child.call_args.args[0]
        self.assertEqual(command[0], str(self.python))
        self.assertIn("--ready", command)
        self.assertIn("--no-browser", command)
        self.assertEqual(command[command.index("--port") + 1], "4174")
        self.assertEqual(child.call_args.kwargs["cwd"], self.root)
        browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
