# Murmur

**Local voice dictation that writes like you do.** Speak into any window, a local LLM cleans up the transcript, and the text is typed where your cursor is. Fully offline: no cloud, no account, no subscription.

Murmur is a Linux alternative to tools like Wispr Flow. The thing that makes those tools feel good is not the raw transcription, it's the cleanup pass that turns "um so like can we uh move the button" into "Can we move the button?" Murmur does that cleanup with a local model, so nothing you say ever leaves your machine.

## Why it exists

- **Wispr Flow has no Linux build.** This fills that gap on Wayland (tested on KDE Plasma 6).
- **Most open-source dictation tools do raw transcription only.** Murmur adds a local LLM cleanup pass (filler removal, punctuation, proper-noun fixes), which is the part that actually makes dictation usable for real writing.
- **Privacy by construction.** Whisper runs locally, the cleanup model runs locally via Ollama. There is no network path for your audio or text.

## How it works

```
mic ──pw-record──▶ faster-whisper (resident in VRAM) ──▶ local LLM cleanup (Ollama)
                                                              │
                                              deterministic corrections.txt
                                                              │
                                                         ydotool types it
```

- `daemon.py` keeps the Whisper model resident on a unix socket, records via PipeWire, trims trailing silence, transcribes, optionally cleans up via Ollama, then types via `ydotool`.
- `dictation-toggle` is a tiny client that tells the daemon to start/stop. Bind it to a keyboard shortcut.
- `mouse-trigger.py` is an optional extra that turns a mouse wheel-flick into start/submit (handy because KDE/Wayland can't bind wheel events to shortcuts).
- `vocab.txt` biases transcription toward your jargon; `corrections.txt` applies deterministic `wrong => right` fixes last.

## Requirements

- Linux with PipeWire (`pw-record`) and a Wayland or X11 session. Text injection uses [`ydotool`](https://github.com/ReimuNotMoe/ydotool).
- An NVIDIA GPU is strongly recommended (the default model is `large-v3-turbo` in float16). CPU works but is slower; pick a smaller `VD_MODEL`.
- [`uv`](https://docs.astral.sh/uv/) for Python dependency management.
- [Ollama](https://ollama.com/) for the cleanup pass (optional; set `VD_CLEANUP=0` to skip it).

## Install

```bash
git clone https://github.com/paretoimproved/murmur.git
cd murmur
./install.sh
```

`install.sh` runs `uv sync`, seeds `vocab.txt`/`corrections.txt` from the examples, and installs + starts a user systemd service. Then bind `dictation-toggle` to a keyboard shortcut (System Settings → Shortcuts on KDE) and you're dictating.

You also need `ydotoold` running with a socket your user can reach. The simplest setup is a root service with the socket owned by your user:

```ini
# /etc/systemd/system/ydotoold.service (drop-in: chown the socket to your uid)
ExecStart=/usr/bin/ydotoold --socket-path=/run/ydotoold.socket --socket-own=<uid>:<gid>
```

## Usage

- Press your toggle shortcut, speak (pause to think as long as you want, it records through silence), press again to stop. The cleaned text types into the focused window.
- `dictation-toggle` toggles; that's the only client you need for keyboard use.

## Configuration

All knobs are `VD_*` environment variables. Set them in the systemd unit (`systemctl --user edit murmur.service`, add `Environment=VD_X=...`), then restart.

| Variable | Default | What it does |
|---|---|---|
| `VD_MODEL` | `large-v3-turbo` | Whisper model. `large-v3` for accuracy, `small.en`/`distil-large-v3` for lower latency. |
| `VD_CLEANUP` | `1` | Local LLM cleanup pass on/off. |
| `VD_CLEANUP_MODEL` | `llama3.1:8b` | Ollama model for cleanup. |
| `VD_CLEANUP_KEEPALIVE` | `10m` | How long to keep the cleanup model warm in VRAM. |
| `VD_SILENCE_HANG` | `0` | `0` = manual stop only. Set seconds to auto-stop after that much quiet (cuts off thinking pauses). |
| `VD_SILENCE_RMS` | `0.012` | Voice-activity threshold. Raise in a noisy room, lower if it clips you. |
| `VD_TRAIL_PAD` | `0.6` | Seconds of audio kept after your last word, before the trailing silence is trimmed. |
| `VD_MAX_SECONDS` | `300` | Hard cap per dictation. |
| `VD_LANG` | `en` | Language. |
| `VD_BEAM` | `1` | Beam size. `5` is marginally more accurate, slower. |
| `VD_TRAILING_SPACE` | `1` | Append a space after each dictation. |
| `VD_MOUSE_NAME` | _(unset)_ | For the optional mouse trigger: device name to target (see `/proc/bus/input/devices`). Empty = first mouse found. |

### Vocabulary and corrections

- `vocab.txt`: one proper noun per line. Biases transcription and protects these terms during cleanup. Copy from `vocab.txt.example` and make it yours.
- `corrections.txt`: deterministic `wrong => right` fixes (case-insensitive, whole words), applied last. Good for terms Whisper reliably mishears.

The cleanup prompt is deliberately conservative: it only fixes a word to a vocab term when it's an obvious phonetic match, and it leaves garbled or repetitive passages untouched rather than inventing plausible text. This prevents the classic failure where trailing-silence hallucinations get "corrected" into real-looking sentences.

## Optional: mouse wheel-flick trigger

KDE/Wayland can't bind mouse-wheel events to shortcuts, so `mouse-trigger.py` reads the mouse evdev node directly: flick the horizontal wheel left to start/stop, right to stop-and-submit (press Enter). It runs as a root service for `/dev/input` access. Set `VD_MOUSE_NAME` to your device and install `systemd/murmur-mouse.service` (see the file for the template). This is a nice-to-have, not required.

## Optional: free VRAM while gaming

`watch-games.sh` (installed as `murmur-gamewatch.service`) polls for a running Steam game and unloads the models while you play, reloading when you quit. Enable with `systemctl --user enable --now murmur-gamewatch.service`.

## GPU notes (CUDA / Blackwell)

ctranslate2 (faster-whisper's backend) needs the CUDA 12 runtime libraries. The pip packages `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` are pinned as dependencies, and `run-daemon.sh` puts them on `LD_LIBRARY_PATH` at launch (the `nvidia` namespace package has no `__file__`, so it resolves via `__path__`). Blackwell / sm_120 GPUs (e.g. RTX 50-series) work on ctranslate2 4.8+.

## License

MIT. See [LICENSE](LICENSE).
