#!/usr/bin/env bash
# Launcher for the murmur daemon: puts the pip-installed CUDA 12
# libraries (cuBLAS/cuDNN) on the loader path, points ydotool at its socket,
# then execs the resident daemon.
set -euo pipefail
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP"

NVLIBS="$(uv run --quiet python -c 'import glob,nvidia; b=list(nvidia.__path__)[0]; print(":".join(sorted(glob.glob(b+"/*/lib"))))')"
export LD_LIBRARY_PATH="${NVLIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export YDOTOOL_SOCKET="${YDOTOOL_SOCKET:-/run/ydotoold.socket}"

exec uv run --quiet python daemon.py
