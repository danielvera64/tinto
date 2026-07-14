"""Terminal keyboard input for the hardware loop.

Lets the reader be driven from the console (including over SSH) in
addition to the HAT buttons. Reads raw keys from stdin on a daemon
thread and pushes the same events the buttons produce:

  Up / Left arrow, p      "up"      previous page / selection up
  Down / Right arrow,
  space, n                "down"    next page / selection down
  Enter, m                "select"  menu / open book
  Backspace, f            "back"    font size / back to book
  q                       "quit"    clear the screen and exit

Does nothing when stdin is not a terminal (e.g. under systemd).
"""

import os
import select
import sys
import termios
import threading
import tty

CHAR_MAP = {
    b"p": "up",
    b"n": "down",
    b" ": "down",
    b"\r": "select",
    b"\n": "select",
    b"m": "select",
    b"f": "back",
    b"\x7f": "back",  # backspace
    b"h": "home",           # home menu, from anywhere
    b"[": "jump-back",      # previous chapter / first item
    b"]": "jump-forward",   # next chapter / last item
    b"g": "alt-up",         # font size (reading)
    b"r": "alt-down",       # full refresh / fetch batch
    b"q": "quit",
}

# Arrow keys arrive as escape sequences: ESC [ <letter>
ARROW_MAP = {
    b"A": "up",     # up arrow
    b"D": "up",     # left arrow
    b"B": "down",   # down arrow
    b"C": "down",   # right arrow
}


class KeyboardListener:
    def __init__(self, event_queue, stdin=None):
        self._queue = event_queue
        self._stdin = stdin if stdin is not None else sys.stdin
        self._fd = self._stdin.fileno()
        self._old_attrs = None
        self._running = False
        self._thread = None

    def start(self):
        if not os.isatty(self._fd):
            return False
        self._old_attrs = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._old_attrs is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)
            self._old_attrs = None

    def _read_key(self):
        ch = os.read(self._fd, 1)
        if ch != b"\x1b":
            return CHAR_MAP.get(ch.lower())
        # Escape sequence: expect "[X" to follow for arrow keys
        seq = b""
        while len(seq) < 2:
            r, _, _ = select.select([self._fd], [], [], 0.05)
            if not r:
                return None  # bare ESC
            seq += os.read(self._fd, 1)
        if seq[:1] == b"[":
            return ARROW_MAP.get(seq[1:2])
        return None

    def _loop(self):
        while self._running:
            r, _, _ = select.select([self._fd], [], [], 0.5)
            if not r:
                continue
            try:
                event = self._read_key()
            except OSError:
                break
            if event:
                self._queue.put(event)


def start_keyboard(event_queue):
    """Returns a started KeyboardListener, or None if stdin is not a tty."""
    listener = KeyboardListener(event_queue)
    return listener if listener.start() else None
