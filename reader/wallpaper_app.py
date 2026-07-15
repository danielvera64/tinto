"""Wallpaper app: a landscape slideshow of local images.

Images come from the wallpapers/ folder next to the app's data (the
directory holding reader_state.json); the folder is created on first
open and rescanned on every render, so files can be added while the
app runs. Frames are composed landscape (264x176) — hold the device
with the buttons at the bottom, like the e-reader — and go through
the same contrast/sharpen/dither pipeline as the manga covers.

Slides advance on the shared "Slide interval" from Settings (the same
value the manga app uses). A clock (HH:MM) is overlaid bottom-center
in red — manga-title style, white halo — and refreshes each minute
with the fast waveform (slide changes use the full waveform); the
panel therefore stays awake while this app is active, like the clock
widget. The last shown image persists in reader_state.json
("wallpaper" key). No footer or hints are drawn. Controls: up/down
previous/next (debounced); select or back returns to the home menu.
"""

import logging
import os
import time

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .layout import load_font
from .ui import BLACK, WHITE

logger = logging.getLogger(__name__)

EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")
NAV_DEBOUNCE = 0.5  # settle time after prev/next presses, in seconds
DEFAULT_MIN = 5     # slide minutes when Settings has no value yet


class WallpaperApp:
    def __init__(self, display, state, data_dir, on_home):
        self.display = display
        self.state = state
        self.on_home = on_home
        self.dir = os.path.join(data_dir, "wallpapers")
        self._last_change = 0.0
        self._minute = None  # "HH:MM" of the last rendered frame
        self.render_due = None  # pending debounced redraw (shell polls)

    # ------------------------------------------------------------ data

    def _images(self):
        try:
            files = sorted(f for f in os.listdir(self.dir)
                           if f.lower().endswith(EXTENSIONS))
        except OSError:
            files = []
        return [os.path.join(self.dir, f) for f in files]

    @property
    def index(self) -> int:
        return self.state.data.get("wallpaper", {}).get("index", 0)

    def _set_index(self, i: int):
        self.state.data["wallpaper"] = {"index": i}
        self.state.save()

    def _change_every(self) -> int:
        """Shared slide interval (seconds), set in the Settings app."""
        return self.state.data.get("manga_refresh_min", DEFAULT_MIN) * 60

    # ------------------------------------------------------------ app API

    def activate(self):
        self.render_due = None
        os.makedirs(self.dir, exist_ok=True)
        self._last_change = time.time()
        self._show(full=True)

    def handle(self, event):
        if event in ("select", "back"):
            self.render_due = None
            self.on_home()
            return
        images = self._images()
        if not images or event not in ("up", "down"):
            return
        # Debounced navigation, like the manga app
        step = -1 if event == "up" else 1
        self._set_index((self.index + step) % len(images))
        self._last_change = time.time()  # chosen slide gets a full stay
        self.render_due = time.time() + NAV_DEBOUNCE

    def tick(self, now, idle_for):
        if self.render_due is not None and now >= self.render_due:
            self.render_due = None
            self._show()
            return
        images = self._images()
        if images and now - self._last_change >= self._change_every():
            self._set_index((self.index + 1) % len(images))
            self._last_change = now
            self._show()
        elif time.strftime("%H:%M") != self._minute:
            self._show(full=False)  # minute tick: fast refresh only

    # ------------------------------------------------------------ drawing

    def _show(self, full=True):
        # Landscape frame; the display layer rotates it onto the panel.
        # Always a full refresh: slides are art, fast refresh muddies
        # the dithering.
        W = max(self.display.width, self.display.height)
        H = min(self.display.width, self.display.height)
        img = Image.new("1", (W, H), WHITE)
        draw = ImageDraw.Draw(img)
        images = self._images()

        if not images:
            font = load_font(14, bold=True)
            for i, line in enumerate(["No wallpapers yet —",
                                      "copy images into the",
                                      "wallpapers/ folder"]):
                w = draw.textlength(line, font=font)
                draw.text(((W - w) // 2, H // 4 + i * 22), line,
                          font=font, fill=BLACK)
        else:
            path = images[self.index % len(images)]
            try:
                photo = Image.open(path).convert("L")
                photo = ImageOps.fit(photo, (W, H), Image.LANCZOS)
                photo = ImageOps.autocontrast(photo, cutoff=2)
                photo = photo.filter(ImageFilter.UnsharpMask(
                    radius=2, percent=180, threshold=2))
                img.paste(photo.convert("1"), (0, 0))  # Floyd-Steinberg
            except Exception as exc:
                logger.warning("cannot load wallpaper %s: %s", path, exc)
                font = load_font(12)
                msg = f"cannot load {os.path.basename(path)[:24]}"
                w = draw.textlength(msg, font=font)
                draw.text(((W - w) // 2, H // 2), msg,
                          font=font, fill=BLACK)

        red = Image.new("1", (W, H), WHITE)  # red overlay plane
        self._draw_clock(img, red, W, H)
        self.display.show(img, full=full, red_image=red)

    def _draw_clock(self, img, red, W, H):
        """HH:MM bottom-center, manga-title style: red strokes over a
        white halo punched into the wallpaper."""
        self._minute = time.strftime("%H:%M")
        font = load_font(11, bold=True)  # same as the manga title
        base_draw = ImageDraw.Draw(img)
        red_draw = ImageDraw.Draw(red)
        w = base_draw.textlength(self._minute, font=font)
        x = (W - w) // 2
        y = H - 20
        base_draw.text((x, y), self._minute, font=font, fill=WHITE,
                       stroke_width=2, stroke_fill=WHITE)
        red_draw.text((x, y), self._minute, font=font, fill=BLACK)
