"""GPIO input: three external gesture buttons with push / long push /
double push.

3-pin button modules (VCC -> 3.3 V, GND -> GND, OUT -> pin).
These modules do NOT drive OUT at idle, so each pin gets an internal
pull resistor opposing its press level; the press direction per pin
comes from button_config.json in the project root (created by
`python3 test_buttons.py --calibrate`; default: pull-down,
pressed=HIGH):

  pin     push      long push       double push
  GPIO4   up        jump-back       alt-up    (font size in reader)
  GPIO27  select    back            home      (home menu, anywhere)
  GPIO22  down      jump-forward    alt-down  (full refresh / fetch)

All events are pushed onto the queue consumed by the main loop.
"""

import json
import os
import threading

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# When chain-loaded as an installed release (<root>/releases/<v>/),
# the calibration lives in the install root, or it would be lost on
# every update; a plain checkout keeps it next to the code.
CONFIG_CANDIDATES = [os.path.join(_APP_DIR, "button_config.json")]
_parent = os.path.dirname(_APP_DIR)
if os.path.basename(_parent) == "releases":
    CONFIG_CANDIDATES.insert(0, os.path.join(
        os.path.dirname(_parent), "button_config.json"))

GESTURE_PINS = {
    4: {"push": "up", "long": "jump-back", "double": "alt-up"},
    27: {"push": "select", "long": "back", "double": "home"},
    22: {"push": "down", "long": "jump-forward", "double": "alt-down"},
}

LONG_TIME = 0.8      # seconds held to count as a long push
DOUBLE_WINDOW = 0.4  # max gap between first release and second press


class GestureDetector:
    """Turns raw press/hold/release callbacks into push gestures.

    A double push is committed as soon as the SECOND press starts
    within double_window of the first release. on_event receives one
    of: "push", "long", "double".
    """

    def __init__(self, on_event, double_window=DOUBLE_WINDOW):
        self.on_event = on_event
        self.double_window = double_window
        self._lock = threading.Lock()
        self._long_fired = False
        self._pending_single = False
        self._ignore_release = False
        self._timer = None

    def attach(self, pin, pull="down"):
        """pull='down' -> pressed = HIGH; pull='up' -> pressed = LOW.
        The internal pull is REQUIRED: these modules don't drive OUT
        at idle, and a floating pin oscillates (phantom presses)."""
        from gpiozero import Button

        btn = Button(pin, pull_up=(pull == "up"), bounce_time=0.03,
                     hold_time=LONG_TIME)
        btn.when_pressed = self._pressed
        btn.when_held = self._held
        btn.when_released = self._released
        return btn  # keep a reference or callbacks are collected

    def _pressed(self):
        emit = None
        with self._lock:
            if self._pending_single:
                # second press inside the window -> double, right now
                self._pending_single = False
                if self._timer:
                    self._timer.cancel()
                self._ignore_release = True
                emit = "double"
        if emit:
            self.on_event(emit)

    def _held(self):
        with self._lock:
            self._long_fired = True
        self.on_event("long")

    def _released(self):
        with self._lock:
            if self._long_fired or self._ignore_release:
                self._long_fired = False   # gesture already reported
                self._ignore_release = False
                return
            # maybe a single; wait for a possible second press
            self._pending_single = True
            self._timer = threading.Timer(self.double_window,
                                          self._single_timeout)
            self._timer.daemon = True
            self._timer.start()

    def _single_timeout(self):
        with self._lock:
            if not self._pending_single:
                return
            self._pending_single = False
        self.on_event("push")


def _load_pull_config():
    for path in CONFIG_CANDIDATES:
        try:
            with open(path) as f:
                return {int(k): v for k, v in json.load(f).items()}
        except (OSError, ValueError):
            continue
    return {}


def start_gesture_buttons(event_queue):
    """Registers the three external gesture buttons. Returns holders
    that must stay referenced. Raises if the pins cannot be claimed."""
    pulls = _load_pull_config()
    holders = []
    for pin, actions in GESTURE_PINS.items():
        def emit(gesture, actions=actions):
            event = actions.get(gesture)
            if event:
                event_queue.put(event)
        det = GestureDetector(emit)
        holders.append((det, det.attach(pin, pulls.get(pin, "down"))))
    return holders
