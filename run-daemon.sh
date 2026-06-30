#!/usr/bin/env bash
# Launcher for the murmur daemon. On NVIDIA installs (the optional [gpu] extra)
# this puts the pip CUDA libs on the loader path; on CPU/AMD installs the nvidia
# package is absent and this is a harmless no-op.
set -euo pipefail
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP"

NVLIBS="$(uv run --quiet python -c 'import glob
try:
    import nvidia
    print(":".join(sorted(glob.glob(list(nvidia.__path__)[0] + "/*/lib"))))
except Exception:
    pass' 2>/dev/null || true)"
if [ -n "${NVLIBS:-}" ]; then
  export LD_LIBRARY_PATH="${NVLIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
export YDOTOOL_SOCKET="${YDOTOOL_SOCKET:-/run/ydotoold.socket}"

exec uv run --quiet python daemon.py
