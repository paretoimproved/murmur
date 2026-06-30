# Murmur

**Local voice dictation that writes like you do.** Speak into any window, a local LLM cleans up the transcript, and the text is typed where your cursor is. Fully offline: no cloud, no account, no subscription.

Murmur is a Linux alternative to tools like Wispr Flow. The thing that makes those tools feel good is not the raw transcription, it's the cleanup pass that turns "um so like can we uh move the button" into "Can we move the button?" Murmur does that cleanup with a local model, so nothing you say ever leaves your machine.

## Why it exists

- **Wispr Flow has no Linux build.** This fills that gap on Wayland (tested on KDE Plasma 6).
- **Most open-source dictation tools do raw transcription only.** Murmur adds a local LLM cleanup pass (filler removal, punctuation, proper-noun fixes), which is the part that actually makes dictation usable for real writing.
- **Privacy by construction.** Whisper runs locally, the cleanup model runs locally via Ollama. There is no network path for your audio or text.

## How it works

```
mic --PortAudio--> faster-whisper (resident) --> local LLM cleanup (Ollama)
                                                       |
                                       deterministic corrections.txt
                                                       |
                                                  ydotool types it
```

- `daemon.py` keeps the Whisper model resident on a unix socket, records via PipeWire, trims trailing silence, transcribes, optionally cleans up via Ollama, then types via `ydotool`.
- `dictation-toggle` is a tiny client that tells the daemon to start/stop. Bind it to a keyboard shortcut.
- `mouse-trigger.py` is an optional extra that turns a mouse wheel-flick into start/submit (handy because KDE/Wayland can't bind wheel events to shortcuts).
- `vocab.txt` biases transcription toward your jargon; `corrections.txt` applies deterministic `wrong => right` fixes last.

## Requirements

- A microphone. Audio goes through PortAudio, so any backend works (PipeWire, PulseAudio, ALSA, JACK). Install the PortAudio system library: `portaudio` (Fedora/Arch) or `libportaudio2` (Debian/Ubuntu).
- A Wayland or X11 session. Text injection uses [`ydotool`](https://github.com/ReimuNotMoe/ydotool) (required on Wayland; works on X11 too).
- An NVIDIA GPU gives the best speed, and `./setup` installs the CUDA libs only then. CPU and AMD work too, just slower; `./setup` auto-picks `small.en` on those.
- [`uv`](https://docs.astral.sh/uv/) for Python dependencies.
- [Ollama](https://ollama.com/) for the cleanup pass (optional; set `cleanup = false` in config to skip it).

## Install

```bash
git clone https://github.com/paretoimproved/murmur.git
cd murmur
./setup
```

`./setup` detects your environment (distro and package manager, GPU vs CPU, audio, ydotool, Ollama, desktop) and does the rest: it asks a few questions, writes `~/.config/murmur/config.toml`, syncs Python deps (with the CUDA extra only on NVIDIA), and installs + starts the user service. Then, behind a **single sudo prompt**, it installs any missing system packages (`portaudio`, `ydotool`, `libnotify`) via your package manager and sets up the root `ydotoold` service with the socket owned by you. Finally it pulls the Ollama cleanup model, binds a `Super+\` shortcut to dictation on GNOME / Sway / Hyprland, and runs the health check.

That's the whole install on the major distro + desktop combos: clone, `./setup`, approve one sudo. Pass `--defaults` for a non-interactive run that skips the privileged steps and prints them instead.

A few honest edges: `uv` and Ollama are prerequisites you install yourself (Murmur never pipes a remote script to your shell); `ydotool` isn't packaged on every distro and may need a source build; and **KDE** needs a one-time manual shortcut bind (System Settings -> Shortcuts -> Custom Shortcut -> Command -> the `dictation-toggle` path), because its CLI binding is unreliable and wants a re-login.

**Check your setup any time** with `./doctor`: it prints a pass/fail for every dependency and the exact fix for anything missing.

**Unusual distro or a setup the wizard doesn't cover?** See [SETUP_WITH_AI.md](SETUP_WITH_AI.md) to hand the install to a coding agent that adapts to your exact machine.

## Desktop app (tray + settings)

Prefer not to live in the terminal? Run:

```bash
murmur gui
```

or launch **Murmur** from your application menu. You get a system-tray icon (click to start or stop dictation, with a live status light), a settings window for the model and cleanup options, and a first-run installer that handles the privileged setup through a graphical password prompt (polkit) instead of `sudo`. This is the no-terminal way to use Murmur day to day.

The GUI needs a system tray (works on KDE and most desktops; GNOME needs an AppIndicator extension). It's newer than the CLI, so if anything looks off, the CLI (`murmur`, `./setup`, `./doctor`) is always there as the fallback.

## Usage

**Dictate:** focus a text field, press your shortcut, speak (pause to think as long as you want, it records through silence), press it again. The text types into the focused window.

**Control it with `murmur`** (installed on your PATH by `./setup`). Run it with no arguments any time for status and the full menu:

| Command | Does |
|---|---|
| `murmur` | status + help |
| `murmur config` | edit settings (model, cleanup, etc.) |
| `murmur doctor` | health check with fixes |
| `murmur logs` | watch transcripts live |
| `murmur restart` | apply config changes (also `start` / `stop` / `status`) |
| `murmur toggle` | dictate without a shortcut (types into the focused window) |

## Configuration

Settings live in `~/.config/murmur/config.toml` (created by `./setup` from [config.toml.example](config.toml.example)). Edit it and restart: `systemctl --user restart murmur.service`. Every key can also be overridden with a `VD_<KEY>` environment variable for power users (env wins over the file), e.g. `VD_MODEL=small.en`.

| `config.toml` key | Default | What it does |
|---|---|---|
| `model` | `large-v3-turbo` | Whisper model. `large-v3` for accuracy, `small.en`/`distil-large-v3` for lower latency or CPU. |
| `compute` | `float16` | `int8` on CPU. |
| `cleanup` | `true` | Local LLM cleanup pass on/off. |
| `cleanup_model` | `llama3.1:8b` | Ollama model for cleanup. |
| `cleanup_keepalive` | `10m` | How long to keep the cleanup model warm in VRAM. |
| `silence_hang` | `0` | `0` = manual stop only. Set seconds to auto-stop after that much quiet (cuts off thinking pauses). |
| `silence_rms` | `0.012` | Voice-activity threshold. Raise in a noisy room, lower if it clips you. |
| `trail_pad` | `0.6` | Seconds of audio kept after your last word, before trailing silence is trimmed. |
| `max_seconds` | `300` | Hard cap per dictation. |
| `lang` | `en` | Language. |
| `beam` | `1` | Beam size. `5` is marginally more accurate, slower. |
| `trailing_space` | `true` | Append a space after each dictation. |

The optional mouse trigger reads one extra setting from the environment only: `VD_MOUSE_NAME` (device name to target, see `/proc/bus/input/devices`; empty = first mouse found).

### Vocabulary and corrections

- `vocab.txt`: one proper noun per line. Biases transcription and protects these terms during cleanup. Copy from `vocab.txt.example` and make it yours.
- `corrections.txt`: deterministic `wrong => right` fixes (case-insensitive, whole words), applied last. Good for terms Whisper reliably mishears.

The cleanup prompt is deliberately conservative: it only fixes a word to a vocab term when it's an obvious phonetic match, and it leaves garbled or repetitive passages untouched rather than inventing plausible text. This prevents the classic failure where trailing-silence hallucinations get "corrected" into real-looking sentences.

## Optional: mouse wheel-flick trigger

KDE/Wayland can't bind mouse-wheel events to shortcuts, so `mouse-trigger.py` reads the mouse evdev node directly: flick the horizontal wheel left to start/stop, right to stop-and-submit (press Enter). It runs as a root service for `/dev/input` access. Set `VD_MOUSE_NAME` to your device and install `systemd/murmur-mouse.service` (see the file for the template). This is a nice-to-have, not required.

## Optional: free VRAM while gaming

`watch-games.sh` (installed as `murmur-gamewatch.service`) polls for a running Steam game and unloads the models while you play, reloading when you quit. Enable with `systemctl --user enable --now murmur-gamewatch.service`.

## GPU notes (CUDA / Blackwell)

GPU acceleration uses ctranslate2 (faster-whisper's backend), which needs the CUDA 12 runtime libraries. Those (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) live in an optional `gpu` extra in `pyproject.toml`; `./setup` installs them only when it detects an NVIDIA GPU (`uv sync --extra gpu`), so CPU, AMD, and ARM installs stay lean. On NVIDIA, `run-daemon.sh` puts those libs on `LD_LIBRARY_PATH` at launch (resolved via the `nvidia` namespace package's `__path__`); on other machines that step is a no-op. Blackwell / sm_120 GPUs (e.g. RTX 50-series) work on ctranslate2 4.8+.

## License

MIT. See [LICENSE](LICENSE).
