"""Settings app: a small menu for device options.

Rows cycle their value on "select" (the HOME button) and persist
immediately to reader_state.json:

  Font size      e-reader body text, 12..22 in steps of 2
  Slide interval minutes between manga/wallpaper slides: 3 / 5 / 10
                 (stored as manga_refresh_min for compatibility)
  Update         opening Settings checks GitHub for a newer release
                 in the background; when one exists this row becomes
                 "Update to vX" and selecting it downloads, installs
                 and restarts (the first update bootstraps the
                 releases/ layout automatically)
  Reboot         reboots the Pi (press twice to confirm; needs
  Power off      passwordless sudo, the Raspberry Pi OS default)
  < Home         back to the home menu

The current app version is shown at the bottom of the screen.
Navigation is debounced like the other menus.
"""

import logging
import subprocess
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
        # armed power action awaiting the confirming second press:
        # ("reboot"|"poweroff", expires_at) or None
        self._confirm = None

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

    def _power_label(self, action, label):
        if self._confirm and self._confirm[0] == action:
            return f"{label} — sure?"
        return label

    def _items(self):
        return [
            f"Font size: {self.state.font_size}",
            f"Slide interval: {self._manga_minutes()} min",
            self._update_label(),
            self._power_label("reboot", "Reboot"),
            self._power_label("poweroff", "Power off"),
            "< Home",
        ]

    def _change_selected(self):
        if self.selection not in (3, 4):
            self._confirm = None  # leaving an armed power row disarms it
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
        elif self.selection in (3, 4):
            self._power_select("reboot" if self.selection == 3
                               else "poweroff")
            return
        else:  # "< Home"
            self.on_home()
            return
        self._render()

    # ------------------------------------------------------------ power

    def _power_select(self, action):
        """First press arms the action ('sure?'); a second press within
        the confirmation window executes it."""
        now = time.time()
        if (self._confirm and self._confirm[0] == action
                and now < self._confirm[1]):
            self._confirm = None
            self._execute_power(action)
            return
        self._confirm = (action, now + 6)
        self._render()

    def _execute_power(self, action):
        verb = "Rebooting…" if action == "reboot" else "Powering off…"
        self.display.show(self.renderer.render_message(verb), full=True)
        self.display.sleep()  # protect the panel before the power cut
        cmd = ["sudo", "-n", "systemctl", action]
        try:
            subprocess.run(cmd, check=True, capture_output=True,
                           timeout=15)
            # the system takes it from here; this process gets killed
        except Exception as exc:
            logger.warning("%s failed: %s", action, exc)
            self.display.show(self.renderer.render_message(
                f"Could not {action} — passwordless sudo needed"),
                full=True)
            self.render_due = time.time() + 5  # back to the menu

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
        if self._confirm and now >= self._confirm[1]:
            self._confirm = None  # confirmation window expired
            self._render()
            return
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
