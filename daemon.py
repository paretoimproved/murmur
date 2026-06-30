#!/usr/bin/env python3
"""Local murmur daemon.

Holds a faster-whisper model resident in VRAM and listens on a unix socket.
A "toggle" message starts recording from the default PipeWire source; a second
"toggle" (or ~SILENCE_HANG seconds of trailing silence) stops it, transcribes
on the GPU, and types the text into the focused window via ydotool.
"""
import json
import os
import queue
import re
import socket
import socketserver
import subprocess
import threading
import time
import sys
import urllib.request

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- config: defaults < ~/.config/murmur/config.toml < VD_* env vars -------
import tomllib

CONFIG_DIR = os.environ.get(
    "MURMUR_CONFIG_DIR",
    os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "murmur"),
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")


def _load_toml():
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"config: ignoring {CONFIG_PATH} ({e})", file=sys.stderr)
        return {}


_TOML = _load_toml()


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def cfg(key, default, cast=str):
    """Resolve a setting: env VD_<KEY> wins, then config.toml, then default."""
    env = os.environ.get("VD_" + key.upper())
    raw = env if env is not None else _TOML.get(key, default)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
SOCK_PATH = os.path.join(RUNTIME, "murmur.sock")
YDOTOOL_SOCKET = os.environ.get("YDOTOOL_SOCKET", "/run/ydotoold.socket")

MODEL_NAME = cfg("model", "large-v3-turbo")
COMPUTE = cfg("compute", "float16")
DEVICE = cfg("device", "auto")   # auto | cuda | cpu
LANGUAGE = cfg("lang", "en")
BEAM = cfg("beam", 1, int)

RATE = 16000
SILENCE_RMS = cfg("silence_rms", 0.012, float)
# >0 = auto-stop after N seconds of quiet; 0 = manual stop only (flick left to end)
SILENCE_HANG = cfg("silence_hang", 0.0, float)
NO_SPEECH_TIMEOUT = cfg("no_speech_timeout", 20.0, float)
MAX_SECONDS = cfg("max_seconds", 300.0, float)
MIN_SECONDS = 0.3                 # ignore blips shorter than this
KEY_DELAY = str(cfg("key_delay", "1"))     # ms between keys
KEY_HOLD = str(cfg("key_hold", "1"))       # ms each key held
TRAILING_SPACE = cfg("trailing_space", True, _truthy)
TRAIL_PAD = cfg("trail_pad", 0.6, float)  # seconds of audio kept after last detected speech

# ---- cleanup / vocabulary -------------------------------------------------
CLEANUP = cfg("cleanup", True, _truthy)
CLEANUP_MODEL = cfg("cleanup_model", "llama3.1:8b")
OLLAMA_URL = cfg("ollama_url", "http://localhost:11434")
CLEANUP_KEEPALIVE = cfg("cleanup_keepalive", "10m")
CLEANUP_TIMEOUT = cfg("cleanup_timeout", 12.0, float)


def _config_file(name):
    """Prefer the user config dir, fall back to the repo dir."""
    p = os.path.join(CONFIG_DIR, name)
    return p if os.path.exists(p) else os.path.join(APP_DIR, name)


def _load_vocab():
    path = _config_file("vocab.txt")
    if not os.path.exists(path):
        return []
    terms = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def _load_corrections():
    path = _config_file("corrections.txt")
    pairs = []
    if not os.path.exists(path):
        return pairs
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=>" not in line:
            continue
        wrong, right = line.split("=>", 1)
        wrong, right = wrong.strip(), right.strip()
        if wrong:
            pairs.append((re.compile(r"\b" + re.escape(wrong) + r"\b", re.I), right))
    return pairs


VOCAB = _load_vocab()
CORRECTIONS = _load_corrections()
INITIAL_PROMPT = ("Glossary: " + ", ".join(VOCAB) + ".") if VOCAB else None
# hotwords biases the decoder toward these terms without seeding the prompt into
# the output the way initial_prompt does on trailing silence (the hallucination bug).
HOTWORDS = (", ".join(VOCAB)) if VOCAB else None


def apply_corrections(text):
    for pat, right in CORRECTIONS:
        text = pat.sub(right, text)
    return text


def ollama_cleanup(text):
    """Conservative local LLM pass: strip fillers, fix punctuation/proper nouns."""
    vocab_line = (", ".join(VOCAB)) if VOCAB else "(none)"
    prompt = (
        "You clean up raw speech-to-text dictation. Apply ONLY these edits:\n"
        "- remove filler words (um, uh, er, like, you know) when they are fillers\n"
        "- fix punctuation, capitalization, and obvious mis-transcriptions\n"
        "- these are known proper nouns; fix the spelling/casing of a word ONLY "
        "when it is already an obvious phonetic match to one of them. NEVER replace "
        "an unrelated, garbled, repeated, or low-confidence word with one of them: "
        + vocab_line + "\n"
        "- if a passage looks garbled, repetitive, or like noise rather than speech, "
        "leave it unchanged; do NOT invent plausible text.\n"
        "Do NOT rephrase, summarize, translate, answer, or add anything. "
        "Preserve the speaker's exact wording and intent. "
        "Never add notes, explanations, labels, or any line starting with 'Note'. "
        "Output ONLY the cleaned text itself, with no quotes or commentary.\n\n"
        "Text: " + text + "\nCleaned:"
    )
    body = json.dumps({
        "model": CLEANUP_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": CLEANUP_KEEPALIVE,
        "options": {"temperature": 0},
    }).encode()
    try:
        req = urllib.request.Request(
            OLLAMA_URL + "/api/generate", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=CLEANUP_TIMEOUT) as r:
            out = json.loads(r.read())["response"].strip()
        # strip any trailing meta-commentary the model appends after a blank line
        out = re.split(r"\n\s*\n", out, maxsplit=1)[0]
        out = re.sub(r"^(cleaned|output|text)\s*:\s*", "", out, flags=re.I)
        out = out.strip().strip('"').strip()
        return out or text
    except Exception as e:
        log(f"cleanup skipped ({e})")
        return text

# ---- state ----------------------------------------------------------------
_lock = threading.Lock()
_recording = False
_stop_event = threading.Event()
_pending_submit = False
model = None


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def notify(msg, ident="murmur"):
    try:
        subprocess.run(
            ["notify-send", "-a", "Voice Dictation", "-t", "1800",
             "-h", f"string:x-canonical-private-synchronous:{ident}", msg],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def inject(text):
    payload = text + (" " if TRAILING_SPACE else "")
    env = dict(os.environ, YDOTOOL_SOCKET=YDOTOOL_SOCKET)
    subprocess.run(
        ["ydotool", "type", "-d", KEY_DELAY, "-H", KEY_HOLD, "-f", "-"],
        input=payload.encode("utf-8"), env=env, check=False,
    )


def press_enter():
    env = dict(os.environ, YDOTOOL_SOCKET=YDOTOOL_SOCKET)
    subprocess.run(["ydotool", "key", "28:1", "28:0"], env=env, check=False)


def submit():
    """End any in-flight dictation and press Enter once its text has been typed.

    If nothing is being dictated, just press Enter immediately.
    """
    global _pending_submit
    with _lock:
        if _recording:
            _pending_submit = True
            _stop_event.set()       # end the dictation now
            return "stop+submit"
    press_enter()
    return "enter"


def _maybe_submit():
    global _pending_submit
    with _lock:
        pending = _pending_submit
        _pending_submit = False
    if pending:
        press_enter()


def record_session():
    """Capture audio with live VAD auto-stop, then transcribe and inject."""
    global _recording
    chunks = []                       # float32 mono blocks
    start = time.monotonic()
    last_voice = None
    speech = False
    block = max(256, RATE // 8)       # ~0.125 s per read
    try:
        log(f"recording from input device: {sd.query_devices(kind='input')['name']}")
    except Exception as e:
        log(f"could not resolve input device: {e}")
    notify("\U0001F3A4  Listening…", "vd-state")
    q = queue.Queue()

    def _cb(indata, frames, time_info, status):
        q.put(indata[:, 0].copy())

    got_audio = False
    try:
        # Callback + queue: pull audio off a queue and check the stop signal on a
        # ~0.1s timer, so stopping works even if the device delivers no frames (a
        # stalled mic must never wedge the loop). Works across PipeWire/Pulse/ALSA/JACK.
        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                            blocksize=block, callback=_cb):
            while True:
                if _stop_event.is_set():
                    break
                now = time.monotonic()
                if (now - start) >= MAX_SECONDS:
                    break
                if not got_audio and (now - start) >= 5.0:
                    log("no audio from the input device after 5s; aborting")
                    notify("…no audio from the mic (check your input device)", "vd-state")
                    break
                if not speech and (now - start) >= NO_SPEECH_TIMEOUT:
                    chunks = []
                    break
                try:
                    a = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                got_audio = True
                chunks.append(a)
                rms = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
                if rms >= SILENCE_RMS:
                    speech = True
                    last_voice = now
                if (SILENCE_HANG > 0 and speech and last_voice
                        and (now - last_voice) >= SILENCE_HANG):
                    break
        # The callback buffers audio the loop hadn't pulled yet, and a final
        # flush lands as the stream closes. Drain it so the end of your sentence
        # survives when you toggle dictation off (otherwise the tail is dropped).
        while not q.empty():
            try:
                chunks.append(q.get_nowait())
            except queue.Empty:
                break
    except Exception as e:
        log(f"audio capture failed: {e}")
        notify("…audio capture error (is an input device available?)", "vd-state")
        _finish()
        return

    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    # Trim the trailing silence between the last detected speech and the manual
    # stop-flick. That silent tail is where Whisper hallucinates (it regurgitates
    # the vocab prompt and loops). Keep a short pad so soft word endings survive.
    if speech and last_voice is not None:
        keep_n = int(((last_voice - start) + TRAIL_PAD) * RATE)
        if 0 < keep_n < audio.size:
            audio = audio[:keep_n]
    if audio.size < int(MIN_SECONDS * RATE):
        notify("…nothing heard", "vd-state")
        _maybe_submit()
        _finish()
        return

    notify("✍  Transcribing…", "vd-state")
    t0 = time.monotonic()
    segments, info = model.transcribe(
        audio, language=LANGUAGE, beam_size=BEAM, vad_filter=True,
        hotwords=HOTWORDS,
        condition_on_previous_text=False,
        temperature=0,
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    raw = text
    if text and CLEANUP:
        text = ollama_cleanup(text)
    if text:
        text = apply_corrections(text)
    log(f"transcribed {audio.size/RATE:.1f}s in {time.monotonic()-t0:.2f}s: "
        f"{raw!r} -> {text!r}")
    if text:
        inject(text)
        notify("✓  Inserted", "vd-state")
    else:
        notify("…no speech detected", "vd-state")
    _maybe_submit()
    _finish()


def _finish():
    global _recording, _pending_submit
    with _lock:
        _recording = False
        _stop_event.clear()
        _pending_submit = False


def toggle():
    global _recording
    with _lock:
        if _recording:
            _stop_event.set()
            return "stopping"
        _recording = True
        _stop_event.clear()
        threading.Thread(target=record_session, daemon=True).start()
        return "recording"


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        cmd = self.rfile.readline().strip().decode(errors="replace")
        if cmd == "toggle":
            self.wfile.write((toggle() + "\n").encode())
        elif cmd == "submit":
            self.wfile.write((submit() + "\n").encode())
        elif cmd == "ping":
            self.wfile.write(b"pong\n")
        elif cmd == "status":
            with _lock:
                st = "recording" if _recording else "idle"
            self.wfile.write((st + "\n").encode())
        else:
            self.wfile.write(b"unknown\n")


class Server(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True


def main():
    global model
    t0 = time.monotonic()
    try:
        log(f"loading {MODEL_NAME} ({COMPUTE}) on {DEVICE}…")
        model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE)
    except Exception as e:
        # No CUDA / wrong compute for this device: fall back to plain CPU so the
        # daemon always starts (a crashed daemon = no socket = "not reachable").
        log(f"load on '{DEVICE}' ({COMPUTE}) failed: {e}; falling back to CPU int8")
        model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    # warm the model so the first real dictation is fast
    model.transcribe(np.zeros(RATE, dtype=np.float32), language=LANGUAGE)
    log(f"model ready in {time.monotonic()-t0:.1f}s")
    if CLEANUP:
        threading.Thread(target=lambda: ollama_cleanup("warm up."),
                         daemon=True).start()
        log(f"cleanup enabled via {CLEANUP_MODEL}")

    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)
    server = Server(SOCK_PATH, Handler)
    os.chmod(SOCK_PATH, 0o600)
    notify("Voice dictation ready", "vd-state")
    log(f"listening on {SOCK_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
