"""Shell: the device's home screen and event router.

Shows the initial menu (E-Reader / Widgets), forwards events to the
active app, and runs the periodic tick that lets apps self-update
(clock minutes, weather refreshes) and lets the panel deep-sleep when
idle. Apps return here via their on_home callback.
"""

import os
import time

from PIL import Image, ImageDraw

from .app import ReaderApp
from .manga_app import MangaApp
from .settings_app import SettingsApp
from .ui import Renderer, BLACK, WHITE, MARGIN
from .widgets_app import WidgetsApp

IDLE_SLEEP_SECONDS = 60
MENU_DEBOUNCE = 0.5  # settle time before a menu redraw, in seconds


START_CHOICES = ["reader", "widgets", "manga", "settings",
                 "clock", "weather", "system"]


class Shell:
    def __init__(self, display, state, books_dir, start=None, on_quit=None):
        self.on_quit = on_quit  # called by "back" on the home menu
        self.display = display
        self.state = state
        self.renderer = Renderer(display.width, display.height,
                                 state.font_size)
        self.reader = ReaderApp(display, state, books_dir,
                                on_home=self.show_home)
        self.widgets = WidgetsApp(display, state, on_home=self.show_home)
        data_dir = os.path.dirname(os.path.abspath(state.path)) or "."
        self.manga = MangaApp(display, state, data_dir,
                              on_home=self.show_home)
        self.settings = SettingsApp(display, state, on_home=self.show_home,
                                    on_restart=self._request_quit)
        self._apps = [("E-Reader", self.reader), ("Widgets", self.widgets),
                      ("Manga", self.manga), ("Settings", self.settings)]
        self.active = None  # None = home menu
        self.selection = 0
        self._last_event = time.time()
        self._render_due = None  # pending debounced home-menu redraw
        if start:
            self._launch(start)
        else:
            self.show_home()

    def _launch(self, start: str):
        """Boots directly into an app or a specific widget. The home
        menu selection is synced so back/home navigation behaves as if
        the user had navigated here themselves."""
        apps = {"reader": self.reader, "widgets": self.widgets,
                "manga": self.manga, "settings": self.settings}
        widgets = {w.name.lower(): i
                   for i, w in enumerate(self.widgets.widgets)}
        if start in widgets:
            self.widgets.idx = widgets[start]
            target = self.widgets
        elif start in apps:
            target = apps[start]
        else:
            self.show_home()
            return
        self.selection = next(i for i, (_, app) in enumerate(self._apps)
                              if app is target)
        self.active = target
        target.activate()

    def _request_quit(self):
        """Exit the app (systemd/the launcher restarts it)."""
        if self.on_quit:
            self.on_quit()

    def show_home(self, full: bool = True):
        self.active = None
        self._render_due = None
        img = self.renderer.render_menu(
            "Tinto", [name for name, _ in self._apps], self.selection,
            hint="UP/DOWN · HOME=open")
        red = Image.new("1", img.size, WHITE)
        self._draw_wine_glass(red, img)
        self.display.show(img, full=full, red_image=red)

    def _draw_wine_glass(self, red, img):
        """A small wine glass next to the title, on the red plane (no
        emoji glyphs exist in the panel's 1-bit font stack). Drawn
        black-on-white in `red`, which the display renders red."""
        measure = ImageDraw.Draw(img)
        x = int(MARGIN + measure.textlength(
            "Tinto", font=self.renderer.title_font)) + 9
        y = MARGIN + 1
        draw = ImageDraw.Draw(red)
        # bowl (filled lower half-circle), stem, base
        draw.pieslice((x, y - 6, x + 12, y + 6), 0, 180, fill=BLACK)
        draw.line((x, y, x + 12, y), fill=BLACK)          # rim
        draw.line((x + 6, y + 6, x + 6, y + 13), fill=BLACK)   # stem
        draw.line((x + 2, y + 13, x + 10, y + 13), fill=BLACK)  # base

    def handle(self, event: str):
        self._last_event = time.time()
        if event == "home":
            # global: return to the home menu from anywhere
            if self.active is not None:
                self.show_home()
            return
        if self.active is not None:
            self.active.handle(event)
            return
        # Menu navigation is debounced: rapid presses only move the
        # selection; the screen redraws once, MENU_DEBOUNCE after the
        # last press, showing the net result.
        if event == "up":
            self.selection = (self.selection - 1) % len(self._apps)
            self._render_due = time.time() + MENU_DEBOUNCE
        elif event == "down":
            self.selection = (self.selection + 1) % len(self._apps)
            self._render_due = time.time() + MENU_DEBOUNCE
        elif event == "jump-back":
            self.selection = 0
            self._render_due = time.time() + MENU_DEBOUNCE
        elif event == "jump-forward":
            self.selection = len(self._apps) - 1
            self._render_due = time.time() + MENU_DEBOUNCE
        elif event == "select":
            self._render_due = None  # acts on the latest selection
            self.active = self._apps[self.selection][1]
            self.active.activate()
        elif event == "back":
            self._request_quit()  # long BTN2 / K4 at home quits the app

    def tick(self):
        """Called by the main loop (interval given by timeout())."""
        now = time.time()
        idle_for = now - self._last_event
        if self.active is not None:
            self.active.tick(now, idle_for)
        elif self._render_due is not None and now >= self._render_due:
            self.show_home(full=False)  # fast refresh for navigation
        elif idle_for > IDLE_SLEEP_SECONDS:
            self.display.sleep()  # no-op if already asleep

    def timeout(self):
        """How long the main loop may block before the next tick."""
        due = []
        if self.active is None:
            if self._render_due is not None:
                due.append(self._render_due)
        else:
            app_due = getattr(self.active, "render_due", None)
            if app_due is not None:
                due.append(app_due)
        if not due:
            return 1.0
        return min(1.0, max(0.05, min(due) - time.time()))
