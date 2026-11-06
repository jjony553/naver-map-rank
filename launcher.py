"""더블클릭 실행기의 진입점. Python 표준 라이브러리로 설치를 준비합니다."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STATE_FILE = ROOT / ".setup-state.json"


def run(command: list[str]) -> None:
    env = dict(os.environ, PIP_CACHE_DIR=str(ROOT / ".pip-cache"), PYTHONUTF8="1")
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode:
        raise RuntimeError("설치를 완료하지 못했습니다. 인터넷 연결을 확인한 뒤 시작하기.cmd를 다시 실행해 주세요.")


def prepare() -> None:
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치해 주세요.")
    if not PYTHON.is_file():
        print("[1/3] 이 프로그램 전용 파이썬 환경을 준비합니다.", flush=True)
        run([sys.executable, "-m", "venv", str(VENV)])
    fingerprint = hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()
    try:
        saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        installed = isinstance(saved, dict) and saved.get("requirements") == fingerprint
    except (OSError, ValueError):
        installed = False
    probe = subprocess.run([str(PYTHON), "-c", "import flask; import playwright.sync_api"], capture_output=True, cwd=ROOT)
    if not installed or probe.returncode:
        print("[2/3] 화면 서버와 네이버 조회 도구를 설치합니다. 첫 실행에만 시간이 걸립니다.", flush=True)
        run([str(PYTHON), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(ROOT / "requirements.txt")])
        STATE_FILE.write_text(json.dumps({"requirements": fingerprint}), encoding="utf-8")
    check = subprocess.run([str(PYTHON), "-c", "import json; from collector import collector_health; print(json.dumps(collector_health()))"], capture_output=True, cwd=ROOT, text=True, encoding="utf-8")
    try:
        ready = check.returncode == 0 and json.loads(check.stdout).get("collectorReady")
    except ValueError:
        ready = False
    if not ready:
        print("[3/3] 사용할 조회 브라우저를 설치합니다.", flush=True)
        run([str(PYTHON), "-m", "playwright", "install", "chromium"])


def existing_app(port: int) -> bool:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/health")
    try:
        # Loopback traffic must stay local even when the environment has an HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1) as response:
            health = json.load(response)
        return isinstance(health, dict) and health.get("service") == "place-rank-python" and health.get("runtime") == "python"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def serve(port: int, open_browser: bool) -> None:
    url = f"http://127.0.0.1:{port}"
    if existing_app(port):
        print(f"이미 실행 중입니다. {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return
    from app import create_app
    from werkzeug.serving import make_server

    try:
        server = make_server("127.0.0.1", port, create_app({"PORT": port}), threaded=True)
    except (OSError, SystemExit) as exc:
        raise RuntimeError(f"{port}번 포트를 다른 프로그램이 사용 중입니다. 해당 프로그램을 종료하거나 python launcher.py --port 4174로 실행해 주세요.") from exc
    print(f"\n플레이스 랭크가 준비됐습니다.\n{url}\n\n① 식당 등록  ② 키워드 입력  ③ 순위 측정 시작\n이 창을 열어 두세요. 종료는 Ctrl+C입니다.\n", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n프로그램을 종료했습니다.", flush=True)
    finally:
        server.server_close()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="플레이스 랭크 설치 및 실행")
    parser.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 생략")
    parser.add_argument("--prepare-only", action="store_true", help="설치만 진행")
    parser.add_argument("--port", type=int, default=4173, help="기본 포트: 4173")
    parser.add_argument("--ready", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("포트는 1024~65535 범위로 지정해 주세요.")
    try:
        if not args.ready:
            prepare()
            if args.prepare_only:
                print("설치 완료. 다음에는 시작하기.cmd를 더블클릭하세요.")
                return 0
            command = [str(PYTHON), str(Path(__file__).resolve()), "--ready", "--port", str(args.port)]
            if args.no_browser:
                command.append("--no-browser")
            return subprocess.call(command, cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1"))
        serve(args.port, not args.no_browser)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"\n실행 안내: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
