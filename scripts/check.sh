#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import ast
from pathlib import Path

for path in [Path("app.py"), *Path("backend").glob("*.py")]:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY

if command -v node >/dev/null 2>&1; then
  node --check web/app.js >/dev/null
fi

required_files=(
  "app.py"
  "backend/render.py"
  "backend/preset.py"
  "backend/asset_library.py"
  "backend/ffmpeg_runner.py"
  "web/index.html"
  "web/style.css"
  "web/app.js"
  "setup_windows.bat"
  "run_app.bat"
  "tools/create_shortcut_windows.ps1"
)

for path in "${required_files[@]}"; do
  test -f "$path"
done

echo "check ok"
