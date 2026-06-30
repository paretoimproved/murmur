# Set up Murmur with an AI agent

Every Linux setup is a little different (distro, GPU, audio stack, compositor, how `ydotoold` is wired). The `./setup` wizard handles the common cases; if yours is unusual, you can hand the whole job to a coding agent like [Claude Code](https://claude.com/claude-code) that adapts to your exact machine.

## How to use

1. Clone the repo and `cd` into it.
2. Open Claude Code (or a similar terminal coding agent) in that directory.
3. Paste the prompt below.
4. Approve each step as it goes. The prompt tells the agent to ask before any `sudo`.

## Safety

This runs an agent with access to your shell, including installing packages and writing a root systemd unit for `ydotoold`. That is real system access. The prompt is scoped to **ask before every privileged command** and to **change nothing outside Murmur's own files and services**. Read what it proposes before approving. If you are not comfortable with that, use `./setup` instead.

## The prompt

```
You are setting up "Murmur", a local voice-dictation tool, on my Linux machine.
The repository is in the current directory. Read README.md, setup, doctor, and
config.toml.example first so you understand the design.

Do this, adapting every step to MY environment:
1. Detect: distro and package manager, NVIDIA GPU or CPU-only, session type
   (Wayland/X11) and desktop, PipeWire, ydotool, uv, and Ollama.
2. Install missing dependencies using MY distro's package manager. Show me each
   install command and ask before running anything with sudo.
3. Write ~/.config/murmur/config.toml from config.toml.example. Pick a model that
   fits my hardware (large-v3-turbo + float16 on a capable NVIDIA GPU; small.en +
   int8 on CPU). Set cleanup=true only if Ollama is installed; otherwise false.
   Seed ~/.config/murmur/vocab.txt and corrections.txt from the examples.
4. Run `uv sync` in the repo.
5. Install and start the user service (the systemd/ templates, with __MURMUR_DIR__
   replaced by this repo's absolute path), via systemctl --user.
6. Set up ydotoold so its socket is reachable by my user (socket-own to my uid:gid).
   Ask before installing the root service.
7. Help me bind a keyboard shortcut on MY desktop to `<repo>/dictation-toggle`.
   If you can do it via the compositor's config, propose the change and ask; if
   not, give me exact click-by-click steps.
8. Run `./doctor` and fix anything it reports as a problem.

Rules: ask before every sudo command. Do not touch files or services unrelated to
Murmur. If a step is ambiguous on my system, ask me rather than guessing. When
done, summarize what you changed and how to start/stop dictation.
```

## After setup

- `./doctor` re-checks health any time.
- Settings live in `~/.config/murmur/config.toml`.
- Logs: `journalctl --user -u murmur.service -f`.
