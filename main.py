#!/usr/bin/env python3
"""Tinto — e-paper reader, widgets and manga frame for the
Waveshare 2.7" panel.

On the Raspberry Pi (with the panel + HAT attached):
    python3 main.py

On a desktop, without hardware:
    python3 main.py --emulate          # Tk window, arrow keys turn pages
    python3 main.py --png              # writes each frame to screen.png
"""

import argparse
import os
import queue
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Tinto — e-paper reader, widgets and manga frame")
    parser.add_argument("--emulate", action="store_true",
                        help="run in a desktop window instead of the panel")
    parser.add_argument("--png", action="store_true",
                        help="render frames to screen.png (no window)")
    parser.add_argument("--start",
                        choices=["reader", "widgets", "manga", "settings",
                                 "clock", "weather", "system"],
                        help="boot directly into an app (reader/widgets/"
                             "manga/settings) or a specific widget (clock/"
                             "weather/system); back still returns to the "
                             "home menu")
    parser.add_argument("--panel", choices=["red", "bw"], default="red",
                        help="panel type: 'red' = 2.7\" HAT (B) tri-color "
                             "(default), 'bw' = plain black/white 2.7\" V2")
    parser.add_argument("--books-dir", default=os.path.join(BASE_DIR, "books"),
                        help="directory containing .epub files")
    parser.add_argument("--state-file",
                        default=os.path.join(BASE_DIR, "reader_state.json"),
                        help="where to store bookmarks and settings")
    return parser.parse_args()


def run_hardware(state, books_dir, panel, start=None):
    from reader.buttons import start_buttons, start_gesture_buttons
    from reader.display import EPDDisplay
    from reader.keyboard import start_keyboard
    from reader.shell import Shell

    events = queue.Queue()
    display = EPDDisplay(panel=panel)  # clears the panel on init
    shell = Shell(display, state, books_dir, start=start,
                  on_quit=lambda: events.put("quit"))
    buttons = start_buttons(events)  # noqa: F841  (must stay referenced)
    try:
        gestures = start_gesture_buttons(events)  # noqa: F841
        print("gesture buttons active on GPIO4/27/22 "
              "(push / long / double)")
    except Exception as exc:
        print(f"gesture buttons unavailable ({exc}); HAT keys still work")
    keyboard = start_keyboard(events)  # None when stdin is not a tty
    if keyboard:
        print("Keyboard: arrows/space nav, Enter=select, f=back, h=home, "
              "[ ]=chapter, g=font, r=refresh, q=quit")

    try:
        while True:
            try:
                # timeout() shortens the wait when a debounced menu
                # redraw is pending, so it flushes on time
                event = events.get(timeout=shell.timeout())
            except queue.Empty:
                shell.tick()  # debounce flush + widgets + idle sleep
                continue
            if event == "quit":
                break
            shell.handle(event)
    except KeyboardInterrupt:
        pass
    finally:
        if keyboard:
            keyboard.stop()  # restore terminal settings
        display.close()  # clear the panel and put it to deep sleep


def run_png(state, books_dir, start=None):
    from reader.display import PNGDisplay
    from reader.shell import Shell

    display = PNGDisplay(path=os.path.join(BASE_DIR, "screen.png"))
    quit_flag = []
    shell = Shell(display, state, books_dir, start=start,
                  on_quit=lambda: quit_flag.append(True))
    print("Rendering to screen.png — commands: n(ext/down), p(rev/up), "
          "m(select), f(back), h(ome), [/](chapter), g(font), "
          "r(efresh), t(ick), q(uit)")
    actions = {"n": "down", "p": "up", "m": "select", "f": "back",
               "h": "home", "[": "jump-back", "]": "jump-forward",
               "g": "alt-up", "r": "alt-down"}
    while True:
        try:
            cmd = input("> ").strip().lower()
        except EOFError:
            break
        if cmd == "q" or quit_flag:
            break
        if cmd == "t":
            shell.tick()
            print("tick")
        elif cmd in actions:
            import time as _time
            shell.handle(actions[cmd])
            if quit_flag:
                break
            _time.sleep(0.6)  # let debounced menu redraws settle
            shell.tick()
            print("updated screen.png")


def main():
    args = parse_args()
    os.makedirs(args.books_dir, exist_ok=True)

    from reader.state import State
    state = State(args.state_file)

    if args.png:
        run_png(state, args.books_dir, args.start)
    elif args.emulate:
        from reader import emulator
        emulator.run(state, args.books_dir, args.start)
    else:
        run_hardware(state, args.books_dir, args.panel, args.start)


if __name__ == "__main__":
    sys.exit(main())
