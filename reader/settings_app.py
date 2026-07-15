"""Settings app: a small menu for device options.

Rows cycle their value on "select" (the HOME button) and persist
immediately to reader_state.json:

  Font size      e-reader body text, 12..22 in steps of 2
  Manga slide    minutes between manga slides: 3 / 5 / 10
  Update         opening Settings checks GitHub for a newer release
                 in the background; when one exists this row becomes
                 "Update to vX" and selecting it downloads, installs
                 and restarts (the first update bootstraps the
                 releases/ layout automatically)
  < Home         back to the home menu

The current app version is shown at the bottom of the screen.
Navigation is debounced like the other menus.
"""

import logging
import threading
import time

from PIL import ImageDraw

from . import updater
from .layout import load_font
from .state import FONT_SIZES
from .ui import Renderer, BLACK, FOOTER_HEIGHT

logger = logging.getLogger(__name__)

app_version = updater.current_version  # bottom-of-screen display

MANGA_MINUTES = [3, 5, 10]
MANGA_DEFAULT_MIN = 5
MENU_DEBOUNCE = 0.5


def _cycle(options, current):
    try:
        return options[(options.index(current) + 1) % len(options)]
    except ValueError:
        return options[0]


class SettingsApp:
    def __init__(self, display, state, on_home, on_restart=None):
        self.display = display
        self.state = state
        self.on_home = on_home
        self.on_restart = on_restart or (lambda: None)
        self.renderer = Renderer(display.width, display.height,
                                 state.font_size)
        self.selection = 0
        self.render_due = None  # pending debounced redraw (shell polls)
        # update-check state: idle | checking | available | none | error
        self._update_state = "idle"
        self._latest = None  # (tag, tarball_url) when available

    # ------------------------------------------------------------ values

    def _manga_minutes(self):
        return self.state.data.get("manga_refresh_min", MANGA_DEFAULT_MIN)

    def _update_label(self):
        return {
            "idle": "Update: check now",
            "checking": "Update: checking…",
            "available": f"Update to {self._latest[0]}"
                         if self._latest else "Update: check now",
            "none": "Update: up to date",
            "error": "Update: check failed",
        }[self._update_state]

    def _items(self):
        return [
            f"Font size: {self.state.font_size}",
            f"Manga slide: {self._manga_minutes()} min",
            self._update_label(),
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
        elif self.selection == 2:
            if self._update_state == "available":
                self._run_update()
                return
            self._start_check()  # manual re-check
        else:  # "< Home"
            self.on_home()
            return
        self._render()

    # ------------------------------------------------------------ update

    def _start_check(self):
        """Checks GitHub for a newer release on a background thread;
        the result re-renders via render_due (picked up by tick())."""
        if self._update_state == "checking":
            return
        self._update_state = "checking"

        def worker():
            tag, url = updater.check_latest()
            if tag is None:
                self._update_state = "error"
            elif updater.is_newer(tag):
                self._latest = (tag, url)
                self._update_state = "available"
            else:
                self._latest = None
                self._update_state = "none"
            self.render_due = time.time()  # flush on the main loop

        threading.Thread(target=worker, daemon=True).start()

    def _run_update(self):
        tag, url = self._latest

        def status(msg):
            self.display.show(self.renderer.render_message(msg),
                              full=False)

        try:
            status(f"Updating to {tag}…")
            updater.download_and_install(tag, url, status_cb=status)
        except Exception as exc:
            logger.warning("update to %s failed: %s", tag, exc)
            self._update_state = "error"
            self.display.show(self.renderer.render_message(
                f"Update failed: {exc}"), full=True)
            self.render_due = time.time() + 5
            return
        self.display.show(self.renderer.render_message(
            f"{tag} installed — restarting"), full=True)
        self.on_restart()  # exit; the launcher boots the new version

    # ------------------------------------------------------------ app API

    def activate(self):
        self.render_due = None
        self._start_check()  # opening Settings checks for updates
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
        # current version, small and centered just above the footer
        draw = ImageDraw.Draw(img)
        font = load_font(10)
        version = app_version()
        w = draw.textlength(version, font=font)
        draw.text(((self.display.width - w) // 2,
                   self.display.height - FOOTER_HEIGHT - 16),
                  version, font=font, fill=BLACK)
        self.display.show(img, full=full)
