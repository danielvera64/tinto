#!/usr/bin/env python3
"""Gesture tester for push buttons on GPIO4, GPIO27 and GPIO22.

These pins don't collide with the Waveshare 2.7" HAT (which claims
GPIO 17/25/24/18, SPI 8/10/11 and keys 5/6/13/19) nor with I2C
(GPIO2/3).

Wiring: 3-pin button modules, VCC -> 3.3 V, GND -> GND, OUT -> GPIO.
These modules do NOT drive OUT at idle (the pin floats), so an
internal pull resistor is enabled; run the one-time calibration to
detect which rail each module drives when pressed:

    python3 test_buttons.py --calibrate   # writes button_config.json
    python3 test_buttons.py --raw         # raw edge/level diagnostics

Then run on the Pi:

    python3 test_buttons.py

then press buttons; every detected gesture is printed:

    push        press and release shorter than LONG_TIME
    long push   held for LONG_TIME or more (fires while still held)
    double push two pushes with the second starting within DOUBLE_WINDOW

Ctrl+C quits.
"""

import json
import os
import signal
import sys
import threading
import time

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "button_config.json")

BUTTONS = {4: "BTN1", 27: "BTN2", 22: "BTN3"}
LONG_TIME = 0.8      # seconds held to count as a long push
DOUBLE_WINDOW = 0.4  # max gap between first release and second press


class GestureDetector:
    """Turns raw press/hold/release callbacks into push gestures.

    A double push is committed as soon as the SECOND press starts
    within double_window of the first release (waiting for the second
    release would make the window feel impossibly tight).

    Wiring (done by attach() for gpiozero, or manually in tests):
      button pressed      -> _pressed()
      hold for LONG_TIME  -> _held()
      button released     -> _released()
    """

    def __init__(self, name, on_event,
                 double_window=DOUBLE_WINDOW):
        self.name = name
        self.on_event = on_event
        self.double_window = double_window
        self._lock = threading.Lock()
        self._long_fired = False
        self._pending_single = False
        self._ignore_release = False
        self._timer = None

    def attach(self, pin, pull="down"):
        """pull='down' -> internal pull-down, pressed = HIGH;
        pull='up' -> internal pull-up, pressed = LOW. The internal
        pull is REQUIRED with these modules: they don't drive OUT at
        idle, and a floating pin oscillates (phantom presses)."""
        from gpiozero import Button

        btn = Button(pin, pull_up=(pull == "up"), bounce_time=0.03,
                     hold_time=LONG_TIME)
        polarity = ("pull-up, pressed=LOW" if pull == "up"
                    else "pull-down, pressed=HIGH")
        btn.when_pressed = self._pressed
        btn.when_held = self._held
        btn.when_released = self._released
        return btn, polarity  # keep the button referenced

    # ---- raw events ----------------------------------------------------

    def _pressed(self):
        emit = None
        with self._lock:
            if self._pending_single:
                # second press inside the window -> double, right now
                self._pending_single = False
                if self._timer:
                    self._timer.cancel()
                self._ignore_release = True
                emit = "double push"
        if emit:
            self.on_event(self.name, emit)

    def _held(self):
        with self._lock:
            self._long_fired = True
        self.on_event(self.name, "long push")

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
        self.on_event(self.name, "push")


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def calibrate():
    """One-time: determines which rail each module drives when pressed
    and saves the right pull direction to button_config.json."""
    from gpiozero import DigitalInputDevice

    print("Calibration: for each button, HOLD it down and press Enter "
          "while holding.\n")
    config = {}
    for pin, name in BUTTONS.items():
        # phase 1: pull-down; a pressed HIGH-driving module reads HIGH
        d = DigitalInputDevice(pin, pull_up=False)
        input(f"HOLD {name} (GPIO{pin}) and press Enter... ")
        pressed_high = bool(d.value)
        d.close()
        if pressed_high:
            config[pin] = "down"
            print(f"  {name}: drives HIGH when pressed -> pull-down\n")
            continue
        # phase 2: pull-up; a pressed LOW-driving module reads LOW
        d = DigitalInputDevice(pin, pull_up=True)
        input(f"KEEP HOLDING {name} and press Enter again... ")
        pressed_low = bool(d.value)  # active-low: value 1 == pin LOW
        d.close()
        if pressed_low:
            config[pin] = "up"
            print(f"  {name}: drives LOW when pressed -> pull-up\n")
        else:
            print(f"  {name}: NO press detected on either polarity — "
                  "check the wiring (OUT really on GPIO{pin}? GND "
                  "connected?). Skipped.\n")
    if config:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        print(f"saved {CONFIG_PATH}: {config}")
        print("run 'python3 test_buttons.py' to verify gestures; the "
              "main app picks this file up automatically.")
    else:
        print("nothing detected; config not written.")


def probe_pin(pin):
    """Classifies a pin: 'driven HIGH', 'driven LOW' or 'FLOATING'.

    Reads the idle level twice, once with a pull-down and once with a
    pull-up. A driven line reads the same both times; a floating line
    just follows whichever pull is applied.
    """
    from gpiozero import DigitalInputDevice
    d = DigitalInputDevice(pin, pull_up=False)   # pull-down, active HIGH
    high_with_pulldown = bool(d.value)
    d.close()
    d = DigitalInputDevice(pin, pull_up=True)    # pull-up, active LOW
    high_with_pullup = not d.value
    d.close()
    if high_with_pulldown and high_with_pullup:
        return "driven HIGH"
    if not high_with_pulldown and not high_with_pullup:
        return "driven LOW"
    return "FLOATING"


def raw_monitor():
    """Prints every raw edge on the pins, bypassing all gesture logic.
    Run with: python3 test_buttons.py --raw"""
    from gpiozero import DigitalInputDevice

    devs = []
    print("probing idle levels (do not press anything)...")
    for pin, name in BUTTONS.items():
        state = probe_pin(pin)
        note = ""
        if state == "FLOATING":
            note = ("  <-- PROBLEM: the module does not drive OUT at "
                    "idle; the pin picks up noise and the boot-time "
                    "polarity sample is random")
        print(f"  {name} GPIO{pin}: {state}{note}")
        # monitor with no pull so we see exactly what the module does
        d = DigitalInputDevice(pin, pull_up=None, active_state=True)
        d.when_activated = (lambda n: lambda: print(
            f"{time.strftime('%H:%M:%S')}  {n}: edge -> HIGH"))(name)
        d.when_deactivated = (lambda n: lambda: print(
            f"{time.strftime('%H:%M:%S')}  {n}: edge -> LOW"))(name)
        devs.append(d)
    print("press each button a few times; every raw edge is printed.")
    print("clean press = one pair of edges. bursts/random edges = "
          "electrical problem. Ctrl+C quits.")
    signal.pause()


def main():
    if "--raw" in sys.argv:
        raw_monitor()
        return
    if "--calibrate" in sys.argv:
        calibrate()
        return

    def report(name, gesture):
        print(f"{time.strftime('%H:%M:%S')}  {name}: {gesture}")

    config = load_config()
    if not config:
        print("no button_config.json — run 'python3 test_buttons.py "
              "--calibrate' once; assuming pull-down/pressed=HIGH")
    holders = []
    for pin, name in BUTTONS.items():
        det = GestureDetector(f"{name} (GPIO{pin})", report)
        try:
            btn, polarity = det.attach(pin, config.get(pin, "down"))
            holders.append((det, btn))
        except Exception as exc:
            print(f"could not claim GPIO{pin}: {exc}")
            return 1
        print(f"listening on GPIO{pin} as {name}  [{polarity}]")

    print(f"push / long push (>{LONG_TIME}s) / double push "
          f"(<{DOUBLE_WINDOW}s gap) — Ctrl+C to quit")
    signal.pause()  # callbacks do the work


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
