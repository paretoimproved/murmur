#!/usr/bin/env python3
"""Murmur GUI: a tray icon, a settings window, and a first-run installer.

Launch with `murmur gui` (which runs it via `uv run --extra gui`). On first run,
if Murmur isn't set up yet, it shows an install window that does the privileged
steps through a graphical polkit password prompt (pkexec) instead of terminal
sudo. After that it lives in the system tray: click to start/stop dictation.
"""
import os
import shutil
import socket
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QMenu, QPushButton, QSystemTrayIcon,
    QTextEdit, QVBoxLayout, QWidget,
)

DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "murmur")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")
SOCK_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "murmur.sock")
YDOTOOL_SOCK = os.environ.get("YDOTOOL_SOCKET", "/run/ydotoold.socket")
MODELS = ["large-v3-turbo", "large-v3", "distil-large-v3", "medium", "small.en", "small", "base.en"]


# ---- small helpers ---------------------------------------------------------
def have(cmd):
    return shutil.which(cmd) is not None


def daemon_cmd(cmd, timeout=2.0):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(SOCK_PATH)
        s.sendall((cmd + "\n").encode())
        data = s.recv(64)
        s.close()
        return data.decode(errors="replace").strip()
    except OSError:
        return None


def svc(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def load_config():
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def write_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)

    def fmt(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return '"%s"' % str(v).replace("\\", "\\\\").replace('"', '\\"')

    lines = ["# Murmur configuration (managed by the Settings window).\n"]
    for k, v in cfg.items():
        lines.append(f"{k} = {fmt(v)}\n")
    with open(CONFIG_PATH, "w") as f:
        f.writelines(lines)


def dot_icon(color):
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(10, 10, 44, 44)
    p.end()
    return QIcon(pm)


def detect_gpu():
    if have("nvidia-smi"):
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True)
        name = (r.stdout or "").strip().splitlines()
        if name:
            return name[0]
    return None


def detect_pm():
    for token, cmd in (("dnf", "dnf"), ("apt", "apt-get"), ("pacman", "pacman"), ("zypper", "zypper")):
        if have(cmd):
            return token
    return None


def has_portaudio():
    # Authoritative: can sounddevice load PortAudio?
    try:
        import sounddevice  # noqa: F401
        return True
    except Exception:
        return False


def missing_packages(pm):
    def pkg(name):
        if name == "portaudio":
            return "libportaudio2" if pm in ("apt", "zypper") else "portaudio"
        if name == "libnotify":
            return {"apt": "libnotify-bin", "zypper": "libnotify-tools"}.get(pm, "libnotify")
        return name
    out = []
    if not has_portaudio():
        out.append(pkg("portaudio"))
    if not have("ydotool"):
        out.append(pkg("ydotool"))
    if not have("notify-send"):
        out.append(pkg("libnotify"))
    return out


def is_setup_done():
    return os.path.exists(CONFIG_PATH) and os.path.exists(YDOTOOL_SOCK)


# ---- first-run install worker ---------------------------------------------
class InstallWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(bool, str)

    def __init__(self, gpu, pm, pkgs, pull_model):
        super().__init__()
        self.gpu, self.pm, self.pkgs, self.pull_model = gpu, pm, pkgs, pull_model

    def run(self):
        try:
            # 1. config
            if not os.path.exists(CONFIG_PATH):
                cfg = {
                    "model": "large-v3-turbo" if self.gpu else "small.en",
                    "compute": "float16" if self.gpu else "int8",
                    "device": "cuda" if self.gpu else "cpu",
                    "cleanup": bool(have("ollama")),
                    "cleanup_model": "llama3.1:8b",
                }
                write_config(cfg)
                self.progress.emit(f"Wrote config: {CONFIG_PATH}")
            for name in ("vocab.txt", "corrections.txt"):
                dst = os.path.join(CONFIG_DIR, name)
                src = os.path.join(DIR, name + ".example")
                if not os.path.exists(dst) and os.path.exists(src):
                    shutil.copy(src, dst)

            # 2. user systemd units
            unit_dir = os.path.expanduser("~/.config/systemd/user")
            os.makedirs(unit_dir, exist_ok=True)
            for u in ("murmur.service", "murmur-gamewatch.service"):
                with open(os.path.join(DIR, "systemd", u)) as f:
                    txt = f.read().replace("__MURMUR_DIR__", DIR)
                with open(os.path.join(unit_dir, u), "w") as f:
                    f.write(txt)
            svc("daemon-reload")
            self.progress.emit("Installed the dictation service.")

            # 3. privileged: packages + ydotoold via pkexec (graphical password)
            self.progress.emit("Requesting permission to install system packages and the input service...")
            helper = os.path.join(DIR, "bin", "murmur-privileged-setup")
            args = ["pkexec", helper, str(os.getuid()), str(os.getgid()), self.pm or "none", *self.pkgs]
            r = subprocess.run(args, capture_output=True, text=True)
            if r.returncode != 0:
                self.finished_ok.emit(False, "Privileged setup was cancelled or failed:\n" + (r.stderr or r.stdout))
                return
            self.progress.emit("System packages and input service are set up.")

            # 4. start the dictation service
            svc("enable", "--now", "murmur.service")

            # 5. optional model pull
            if self.pull_model and have("ollama"):
                self.progress.emit("Pulling the cleanup model (this can take a while)...")
                subprocess.run(["ollama", "pull", "llama3.1:8b"], check=False)

            self.finished_ok.emit(True, "Murmur is installed.")
        except Exception as e:  # pragma: no cover
            self.finished_ok.emit(False, f"Setup failed: {e}")


class SetupWindow(QDialog):
    done = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Murmur, first-time setup")
        self.setMinimumWidth(520)
        self.gpu = detect_gpu()
        self.pm = detect_pm()
        self.pkgs = missing_packages(self.pm)

        v = QVBoxLayout(self)
        v.addWidget(QLabel("<b>Welcome to Murmur</b>, local voice dictation."))
        plan = [
            f"Transcription: {'GPU (' + self.gpu + ')' if self.gpu else 'CPU (small.en)'}",
            "Install the dictation service (no password)",
            ("Install system packages: " + ", ".join(self.pkgs)) if self.pkgs else "System packages: already present",
            "Set up the input service (asks for your password once)",
            "Cleanup: " + ("on (Ollama found)" if have("ollama") else "off (install Ollama later for cleaner text)"),
        ]
        v.addWidget(QLabel("This will:\n  - " + "\n  - ".join(plan)))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        v.addWidget(self.log)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel = QPushButton("Cancel")
        self.install = QPushButton("Install")
        row.addWidget(self.cancel)
        row.addWidget(self.install)
        v.addLayout(row)
        self.cancel.clicked.connect(self.reject)
        self.install.clicked.connect(self._go)

    def _go(self):
        self.install.setEnabled(False)
        self.cancel.setEnabled(False)
        self.worker = InstallWorker(self.gpu, self.pm, self.pkgs, have("ollama"))
        self.worker.progress.connect(lambda m: self.log.append(m))
        self.worker.finished_ok.connect(self._done)
        self.worker.start()

    def _done(self, ok, msg):
        self.log.append(msg)
        if ok:
            QMessageBox.information(self, "Murmur", "Setup complete. Murmur now lives in your system tray, click it to dictate.")
            self.done.emit(True)
            self.accept()
        else:
            QMessageBox.warning(self, "Murmur", msg)
            self.install.setEnabled(True)
            self.cancel.setEnabled(True)


class SettingsDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Murmur, settings")
        self.setMinimumWidth(420)
        cfg = load_config()
        form = QFormLayout(self)

        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(MODELS)
        self.model.setCurrentText(str(cfg.get("model", "small.en")))
        form.addRow("Whisper model", self.model)

        self.device = QComboBox()
        self.device.addItems(["auto", "cuda", "cpu"])
        self.device.setCurrentText(str(cfg.get("device", "auto")))
        form.addRow("Device", self.device)

        self.cleanup = QCheckBox("Clean up transcripts with a local LLM (needs Ollama)")
        self.cleanup.setChecked(bool(cfg.get("cleanup", False)))
        form.addRow(self.cleanup)

        self.cleanup_model = QLineEdit(str(cfg.get("cleanup_model", "llama3.1:8b")))
        form.addRow("Cleanup model", self.cleanup_model)

        self.trailing = QCheckBox("Add a space after each dictation")
        self.trailing.setChecked(bool(cfg.get("trailing_space", True)))
        form.addRow(self.trailing)

        keyb = QPushButton("Set up keyboard shortcut...")
        keyb.clicked.connect(lambda: KeybindDialog().exec())
        form.addRow("Keyboard shortcut", keyb)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        save = QPushButton("Save and restart")
        row.addWidget(cancel)
        row.addWidget(save)
        form.addRow(row)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)

    def _save(self):
        cfg = load_config()
        cfg["model"] = self.model.currentText().strip()
        cfg["device"] = self.device.currentText().strip()
        cfg["cleanup"] = self.cleanup.isChecked()
        cfg["cleanup_model"] = self.cleanup_model.text().strip()
        cfg["trailing_space"] = self.trailing.isChecked()
        cfg.setdefault("compute", "int8" if cfg.get("device") == "cpu" else "float16")
        write_config(cfg)
        svc("restart", "murmur.service")
        QMessageBox.information(self, "Murmur", "Saved. The dictation service restarted with the new settings.")
        self.accept()


class KeybindDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Murmur, keyboard shortcut")
        self.setMinimumWidth(540)
        toggle = os.path.join(DIR, "dictation-toggle")
        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "A keyboard shortcut is the reliable way to dictate: unlike clicking the\n"
            "tray, a key never changes which window is focused, so the text lands where\n"
            "your cursor is."))
        v.addWidget(QLabel("<b>1.</b> Copy this command:"))
        row = QHBoxLayout()
        field = QLineEdit(toggle)
        field.setReadOnly(True)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(toggle),
                                      copy.setText("Copied")))
        row.addWidget(field)
        row.addWidget(copy)
        v.addLayout(row)
        v.addWidget(QLabel(
            "<b>2.</b> Open keyboard settings, add a custom shortcut that runs that\n"
            "command, and assign it a key (Super+\\ is the convention)."))
        openb = QPushButton("Open keyboard settings")
        openb.clicked.connect(self._open_settings)
        v.addWidget(openb)
        note = QLabel("<i>KDE: System Settings - Shortcuts - Add Command. "
                      "You may need to log out and back in for it to take effect.</i>")
        note.setWordWrap(True)
        v.addWidget(note)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)

    def _open_settings(self):
        desk = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        candidates = []
        if "kde" in desk or "plasma" in desk:
            candidates = [["systemsettings", "kcm_keys"], ["kcmshell6", "kcm_keys"],
                          ["systemsettings5", "kcm_keys"]]
        elif "gnome" in desk:
            candidates = [["gnome-control-center", "keyboard"]]
        for c in candidates:
            if shutil.which(c[0]):
                subprocess.Popen(c)
                return
        QMessageBox.information(self, "Murmur",
                               "Open your desktop's keyboard-shortcut settings manually, "
                               "then add a command shortcut for the copied path.")


class Tray(QSystemTrayIcon):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.icons = {"idle": dot_icon("#8a8a8a"), "rec": dot_icon("#e0392b"), "down": dot_icon("#444444")}
        self.setIcon(self.icons["idle"])
        menu = QMenu()
        self.status_action = QAction("Murmur")
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addSeparator()
        act_toggle = QAction("Start / stop dictation", triggered=self.toggle)
        menu.addAction(act_toggle)
        menu.addAction(QAction("Settings...", menu, triggered=self.settings))
        menu.addSeparator()
        menu.addAction(QAction("Quit", menu, triggered=app.quit))
        self.setContextMenu(menu)
        self.activated.connect(self._activated)
        self.setToolTip("Murmur")
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)
        self.refresh()

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.MiddleClick):
            self.toggle()

    def toggle(self):
        if daemon_cmd("toggle") is None:
            self.showMessage("Murmur", "Dictation service isn't running.", QSystemTrayIcon.Warning, 4000)

    def keybind(self):
        KeybindDialog().exec()

    def settings(self):
        SettingsDialog().exec()

    def refresh(self):
        st = daemon_cmd("status")
        if st == "recording":
            self.setIcon(self.icons["rec"]); self.status_action.setText("Listening..."); self.setToolTip("Murmur, listening")
        elif st == "idle":
            self.setIcon(self.icons["idle"]); self.status_action.setText("Idle, ready"); self.setToolTip("Murmur, idle")
        else:
            self.setIcon(self.icons["down"]); self.status_action.setText("Service not running"); self.setToolTip("Murmur, service down")


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Murmur", "No system tray is available in this session.")
        return 1

    if not is_setup_done():
        w = SetupWindow()
        if w.exec() != QDialog.Accepted:
            return 0

    tray = Tray(app)
    tray.show()
    tray.showMessage("Murmur", "Running in the tray. Click to start or stop dictation.",
                     QSystemTrayIcon.Information, 4000)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
