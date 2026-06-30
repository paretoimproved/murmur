#!/usr/bin/env bash
# Murmur installer: sync deps, seed config, install + start the user service.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Murmur install dir: $DIR"

command -v uv >/dev/null || { echo "ERROR: 'uv' not found. Install from https://docs.astral.sh/uv/ then re-run."; exit 1; }
command -v pw-record >/dev/null || echo "WARN: 'pw-record' (PipeWire) not found; audio capture needs it."
command -v ydotool   >/dev/null || echo "WARN: 'ydotool' not found; text injection needs it (and ydotoold running)."
command -v ollama    >/dev/null || echo "NOTE: 'ollama' not found; cleanup pass will fail unless installed (or set VD_CLEANUP=0)."

echo "Syncing Python dependencies (uv sync)..."
( cd "$DIR" && uv sync )

# Seed editable config from the examples on first install.
[ -f "$DIR/vocab.txt" ]       || cp "$DIR/vocab.txt.example" "$DIR/vocab.txt"
[ -f "$DIR/corrections.txt" ] || cp "$DIR/corrections.txt.example" "$DIR/corrections.txt"

# Install user systemd units with the resolved repo path.
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
for u in murmur.service murmur-gamewatch.service; do
  sed "s#__MURMUR_DIR__#$DIR#g" "$DIR/systemd/$u" > "$UNIT_DIR/$u"
done
systemctl --user daemon-reload
systemctl --user enable --now murmur.service

echo
echo "Murmur is installed and running."
echo "  Logs:   journalctl --user -u murmur.service -f"
echo "  Toggle: $DIR/dictation-toggle   (bind this to a keyboard shortcut)"
echo
echo "Still needed:"
echo "  - ydotoold must be running with a socket your user can reach (see README)."
echo
echo "Optional:"
echo "  - Game auto-pause: systemctl --user enable --now murmur-gamewatch.service"
echo "  - Mouse wheel-flick trigger: edit systemd/murmur-mouse.service and install it (see README)."
