"""가상환경·캐시·계정 정보 없이 배포 가능한 Python 실행 파일만 묶습니다."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
files = ["app.py", "collector.py", "locations.py", "launcher.py", "requirements.txt", "README.md", "시작하기.cmd",
         "dist/locations.json", "dist/regions.json", "dist/regions.mjs", "dist/index.html", "dist/app.js", "dist/core.mjs", "dist/styles.css",
         "tests/test_collector.py", "tests/test_server.py", "tests/test_launcher.py"]
output = root / "dist/downloads/place-rank-python.zip"
output.parent.mkdir(parents=True, exist_ok=True)
for name in files:
    if not (root / name).is_file():
        raise SystemExit(f"Missing file: {name}")
with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
    for name in files:
        archive.write(root / name, "place-rank-studio/" + name)
with ZipFile(output) as archive:
    if archive.testzip():
        raise SystemExit("ZIP verification failed")
print(output)
