#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
python_bin="${PYTHON:-}"
if [ -z "$python_bin" ]; then
  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  else
    python_bin="python"
  fi
fi

command=("$python_bin" "$script_dir/run_ui_smoke.py" "$@")

cd "$repo_root"
if [ -z "${DISPLAY:-}" ] && command -v xvfb-run >/dev/null 2>&1; then
  exec xvfb-run -a "${command[@]}"
fi

exec "${command[@]}"
