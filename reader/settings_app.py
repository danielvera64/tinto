"""Settings app: a small menu for device options.

Rows cycle their value on "select" (the HOME button) and persist
immediately to reader_state.json:

  Font size      e-reader body text, 12..22 in steps of 2
  Manga slide    minutes between manga slides: 3 / 5 / 10
  < Home         back to the home menu

Navigation is debounced like the other menus.
"""

import time

from .state import FONT_SIZES
from .ui import Renderer

MANGA_MINUTES = [3, 5, 10]
MANGA_DEFAULT_MIN = 5
MENU_DEBOUNCE = 0.5


def _cycle(options, current):
    try:
        return options[(options.index(current) + 1) % len(options)]
    except ValueError:
        return options[0]


class SettingsApp:
    def __init__(self, display, state, on_home):
        self.display = display
        self.state = state
        self.on_home = on_home
        self.renderer = Renderer(display.width, display.height,
                                 state.font_size)
        self.selection = 0
        self.render_due = None  # pending debounced redraw (shell polls)

    # ------------------------------------------------------------ values

    def _manga_minutes(self):
        return self.state.data.get("manga_refresh_min", MANGA_DEFAULT_MIN)

    def _items(self):
        return [
            f"Font size: {self.state.font_size}",
            f"Manga slide: {self._manga_minutes()} min",
            "< Home",
        ]

    def _change_selected(self):
        if self.selection == 0:
            self.state.data["font_size"] = _cycle(FONT_SIZES,
                                                  self.state.font_size)
            self.state.save()
        elif self.selection == 1:
            self.state.data["manga_refresh_min"] = _cycle(
                MANGA_MINUTES, self._manga_minutes())
            self.state.save()
        else:  # "< Home"
            self.on_home()
            return
        self._render()

    # ------------------------------------------------------------ app API

    def activate(self):
        self.render_due = None
        self._render(full=True)

    def handle(self, event):
        count = len(self._items())
        if event == "up":
            self.selection = (self.selection - 1) % count
            self.render_due = time.time() + MENU_DEBOUNCE
        elif event == "down":
            self.selection = (self.selection + 1) % count
            self.render_due = time.time() + MENU_DEBOUNCE
        elif event == "jump-back":
            self.selection = 0
            self.render_due = time.time() + MENU_DEBOUNCE
        elif event == "jump-forward":
            self.selection = count - 1
            self.render_due = time.time() + MENU_DEBOUNCE
        elif event == "select":
            self.render_due = None
            self._change_selected()
        elif event == "back":
            self.render_due = None
            self.on_home()

    def tick(self, now, idle_for):
        if self.render_due is not None and now >= self.render_due:
            self._render()
        elif idle_for > 60:
            self.display.sleep()  # no-op if already asleep

    def _render(self, full=False):
        self.render_due = None
        img = self.renderer.render_menu(
            "Settings", self._items(), self.selection,
            hint="UP/DOWN · HOME=change")
        self.display.show(img, full=full)
