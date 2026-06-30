#!/usr/bin/env python3
"""Watch the gaming mouse for a horizontal-wheel left-flick and toggle dictation.

KDE/Wayland can't bind mouse-wheel events to shortcuts, so this reads the mouse's
evdev node directly (REL_HWHEEL < 0 = flick left) and sends "toggle" to the
murmur daemon socket. Runs as a root system service so it needs no
re-login for /dev/input access; root can reach the user-owned daemon socket.
"""
import glob
import os
import socket
import struct
import sys
import time

DEVICE_NAME = os.environ.get("VD_MOUSE_NAME", "")  # optional: target a specific mouse; empty = first mouse found
USER_UID = int(os.environ.get("VD_UID", "1000"))
SOCKET_PATH = os.environ.get("VD_SOCKET", f"/run/user/{USER_UID}/murmur.sock")
COOLDOWN = float(os.environ.get("VD_TRIGGER_COOLDOWN", "0.7"))

EV_KEY = 0x01
EV_REL = 0x02
REL_HWHEEL = 0x06
FMT = "llHHi"
SZ = struct.calcsize(FMT)  # 24 on 64-bit


def find_device():
    try:
        blocks = open("/proc/bus/input/devices").read().split("\n\n")
    except OSError:
        return None
    for b in blocks:
        if DEVICE_NAME in b and "mouse" in b:
            for line in b.splitlines():
                if line.startswith("H: Handlers="):
                    for tok in line.split("=", 1)[1].split():
                        if tok.startswith("event"):
                            return "/dev/input/" + tok
    return None


def send_cmd(cmd):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(SOCKET_PATH)
        s.sendall(cmd.encode() + b"\n")
        s.recv(64)
        s.close()
    except OSError as e:
        print(f"{cmd} failed ({e}); is the daemon running / user logged in?",
              file=sys.stderr, flush=True)


def listen(dev):
    last_left = 0.0
    last_right = 0.0
    with open(dev, "rb", buffering=0) as f:
        print(f"listening on {dev}", file=sys.stderr, flush=True)
        while True:
            data = f.read(SZ)
            if not data or len(data) < SZ:
                return  # device went away; service restarts us
            _, _, etype, code, value = struct.unpack(FMT, data)
            if etype != EV_REL or code != REL_HWHEEL:
                continue
            now = time.monotonic()
            if value < 0 and now - last_left >= COOLDOWN:        # flick left
                last_left = now
                print("left-flick -> toggle", file=sys.stderr, flush=True)
                send_cmd("toggle")
            elif value > 0 and now - last_right >= COOLDOWN:     # flick right
                last_right = now
                print("right-flick -> submit", file=sys.stderr, flush=True)
                send_cmd("submit")


def main():
    while True:
        dev = find_device()
        if not dev:
            time.sleep(2)
            continue
        try:
            listen(dev)
        except OSError as e:
            print(f"read error on {dev}: {e}", file=sys.stderr, flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
