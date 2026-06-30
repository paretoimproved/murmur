#!/usr/bin/env bash
# Auto-pause murmur while ANY Steam game is running, resume when none are.
# Steam wraps every game launch (native and Proton) in "reaper SteamLaunch AppId=",
# so this one watcher covers all installed games with no per-game config.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATTERN="reaper SteamLaunch AppId="
INTERVAL="${VD_GAMEWATCH_INTERVAL:-4}"
state=0   # 0 = no game, 1 = game running

while true; do
    if pgrep -f "$PATTERN" >/dev/null 2>&1; then
        if [ "$state" -eq 0 ]; then
            state=1
            "$DIR/dictation-pause"
        fi
    else
        if [ "$state" -eq 1 ]; then
            state=0
            "$DIR/dictation-resume"
        fi
    fi
    sleep "$INTERVAL"
done
