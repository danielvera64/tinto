"""Display backends.

EPDDisplay drives the Waveshare 2.7" panel through BulkEPD, a subclass
of the stock epd2in7_V2 driver that sends the framebuffer as one bulk
SPI transfer (writebytes2) instead of one byte at a time through Python
-- the stock loop alone costs on the order of a second per update.

Two panel types are supported:

  panel="red"  -- 2.7" HAT (B), the black/white/RED panel. On this
      hardware the controller RAM at 0x26 is the RED plane, so it must
      be kept zeroed or its contents show up red (writing the page
      image there paints the whole background red). It is driven with
      the B/W waveforms: fast refresh for page turns, full refresh
      every FULL_REFRESH_EVERY turns to clear ghosting. The panel's
      own partial-refresh mode cannot be used: it diffs against 0x26,
      which this panel displays as red.

  panel="bw"   -- the plain B/W 2.7" V2 panel. Page turns use partial
      refresh (~0.3 s, no black flash); 0x26 holds the reference frame
      written by display_Base.

The panel is put into deep sleep when idle (see App) -- e-paper must
not be left powered. Waking from deep sleep loses the controller RAM,
so the first frame afterwards is always a full refresh.

show() takes an optional red_image (1-bit, drawn black-on-white like
the main image). On the tri-color panel it is written to the 0x26 red
plane and renders in actual red; on the B/W panel it is merged into
the black image; PNGDisplay composites it in red for development.

PNGDisplay just writes the frame to a .png so the reader can be
developed without hardware.
"""

from PIL import Image, ImageChops

WIDTH = 176   # panel native portrait width
HEIGHT = 264

FULL_REFRESH_EVERY = 12

_PLANE_BW = 0x24   # black/white image RAM
_PLANE_ALT = 0x26  # red plane on the (B) panel; base frame on the B/W panel


def make_bulk_epd():
    from waveshare_epd import epd2in7_V2
    epdconfig = epd2in7_V2.epdconfig

    class BulkEPD(epd2in7_V2.EPD):
        @property
        def frame_bytes(self):
            return ((self.width + 7) // 8) * self.height

        def getbuffer(self, image):
            """Fast replacement for the stock per-pixel Python loop.
            Accepts portrait (176x264) images as-is; landscape
            (264x176) images are rotated so their left edge lands at
            the top of the panel -- hold the device with the buttons
            at the bottom."""
            from PIL import Image as PILImage
            if image.size == (self.height, self.width):
                image = image.transpose(PILImage.ROTATE_270)
            if image.size != (self.width, self.height):
                raise ValueError(f"unexpected image size {image.size}")
            # mode "1" packs 8 px/byte MSB-first, white=1 -- exactly
            # the panel's buffer layout (width 176 = 22 whole bytes)
            return bytearray(image.convert("1").tobytes())

        def _send_data_bulk(self, data):
            epdconfig.digital_write(self.dc_pin, 1)
            epdconfig.digital_write(self.cs_pin, 0)
            epdconfig.spi_writebyte2(data)
            epdconfig.digital_write(self.cs_pin, 1)

        def _set_cursor(self):
            self.send_command(0x4E)  # RAM x counter
            self.send_data(0x00)
            self.send_command(0x4F)  # RAM y counter
            self.send_data(0x00)
            self.send_data(0x00)

        def write_plane(self, plane, data):
            """Bulk-writes a full frame into one RAM plane (no refresh)."""
            self._set_cursor()
            self.send_command(plane)
            self._send_data_bulk(data)

        def _quick_reset(self):
            # Same shape as the stock reset() but ms-scale delays (the
            # datasheet needs far less than the stock 2x200 ms) and a
            # busy-wait so we never talk to a controller mid-reset.
            epdconfig.digital_write(self.reset_pin, 1)
            epdconfig.delay_ms(20)
            epdconfig.digital_write(self.reset_pin, 0)
            epdconfig.delay_ms(2)
            epdconfig.digital_write(self.reset_pin, 1)
            epdconfig.delay_ms(20)
            self.ReadBusy()

        # ---- black/white refreshes (0x24 only) --------------------------
        def display_bw(self, image):
            self.write_plane(_PLANE_BW, image)
            self.TurnOnDisplay()

        def display_bw_fast(self, image):
            self.write_plane(_PLANE_BW, image)
            self.TurnOnDisplay_Fast()

        # ---- B/W-panel-only refreshes (0x26 = reference frame) ----------
        def display_base(self, image):
            self.write_plane(_PLANE_BW, image)
            self.write_plane(_PLANE_ALT, image)
            self.TurnOnDisplay()

        def display_partial_full(self, image):
            """Full-screen partial refresh: fast, no black flash."""
            self._quick_reset()

            self.send_command(0x3C)  # BorderWaveform
            self.send_data(0x80)

            self.send_command(0x44)  # RAM x window: full width
            self.send_data(0x00)
            self.send_data((self.width + 7) // 8 - 1)   # 0x15 = 21
            self.send_command(0x45)  # RAM y window: full height
            self.send_data(0x00)
            self.send_data(0x00)
            self.send_data((self.height - 1) & 0xFF)    # 0x07, 0x01 = 263
            self.send_data(((self.height - 1) >> 8) & 0x01)

            self.write_plane(_PLANE_BW, image)
            self.TurnOnDisplay_Partial()

    return BulkEPD()


class EPDDisplay:
    def __init__(self, panel: str = "red"):
        if panel not in ("red", "bw"):
            raise ValueError(f"unknown panel type: {panel!r}")
        self.panel = panel
        self._epd = make_bulk_epd()
        self.width = self._epd.width    # 176
        self.height = self._epd.height  # 264
        self._asleep = False
        self._mode = None           # 'full' | 'fast' (red panel path)
        self._needs_full = True     # next frame must be a full refresh
        self._count = 0
        self._init_full()
        self.clear()

    # ---------------------------------------------------------- internals

    def _init_full(self):
        self._epd.init()
        if self.panel == "red":
            # Anything in the 0x26 plane shows as red on this panel --
            # zero it after every init (a reset may scramble RAM).
            self._epd.write_plane(_PLANE_ALT,
                                  [0x00] * self._epd.frame_bytes)
        self._mode = "full"

    def _init_fast(self):
        self._epd.init_Fast()
        if self.panel == "red":
            self._epd.write_plane(_PLANE_ALT,
                                  [0x00] * self._epd.frame_bytes)
        self._mode = "fast"

    def clear(self, cycles: int = 1):
        """Blank the panel to white.

        On the red panel the red plane is zeroed explicitly EVERY time
        (the last frame may have put red content there -- never assume
        it is clean), and the pixels are exercised black->white:
        red pigment that is physically on display needs the drive
        cycle to come out, a single white pass can leave it visible.
        """
        white = [0xFF] * self._epd.frame_bytes
        if self._mode != "full":
            self._init_full()
        if self.panel == "red":
            self._epd.write_plane(_PLANE_ALT,
                                  [0x00] * self._epd.frame_bytes)
            black = [0x00] * self._epd.frame_bytes
            for _ in range(cycles):
                self._epd.display_bw(black)
                self._epd.display_bw(white)
        else:
            self._epd.display_base(white)
        self._needs_full = True
        self._count = 0

    # ---------------------------------------------------------- interface

    def show(self, image, full: bool = False, red_image=None):
        if self._asleep:
            self._asleep = False
            self._mode = None         # deep sleep wiped the controller RAM
            self._needs_full = True

        full = full or self._needs_full or self._count >= FULL_REFRESH_EVERY

        if self.panel == "red":
            if full:
                if self._mode != "full":
                    self._init_full()
            elif self._mode != "fast":
                self._init_fast()
            # Red plane: 1-bits render red. red_image is drawn
            # black-on-white, so invert its buffer; no red -> zeros.
            if red_image is not None:
                red_buf = [b ^ 0xFF
                           for b in self._epd.getbuffer(red_image)]
            else:
                red_buf = [0x00] * self._epd.frame_bytes
            self._epd.write_plane(_PLANE_ALT, red_buf)
            buf = self._epd.getbuffer(image)
            if full:
                self._epd.display_bw(buf)
                self._count = 0
            else:
                self._epd.display_bw_fast(buf)
                self._count += 1
        else:
            if red_image is not None:
                # No red pigment on this panel: merge as black
                image = ImageChops.logical_and(image, red_image)
            buf = self._epd.getbuffer(image)
            if self._mode != "full":
                self._init_full()
            if full:
                self._epd.display_base(buf)  # rewrites the 0x26 reference
                self._count = 0
            else:
                self._epd.display_partial_full(buf)
                self._count += 1
        self._needs_full = False

    def sleep(self):
        if not self._asleep:
            self._epd.sleep()
            self._asleep = True

    def close(self):
        if self._asleep:
            self._mode = None
        # Two exercise cycles on quit: the screen very likely holds red
        # content (e.g. a manga title) at this point.
        self.clear(cycles=2)
        self._epd.sleep()
        from waveshare_epd import epd2in7_V2
        epd2in7_V2.epdconfig.module_exit(cleanup=True)


def compose_red(image, red_image):
    """Development preview: renders the red plane in actual red."""
    out = image.convert("L").convert("RGB")
    if red_image is not None:
        red_layer = Image.new("RGB", out.size, (200, 30, 30))
        mask = red_image.convert("L").point(lambda v: 255 if v < 128 else 0)
        out = Image.composite(red_layer, out, mask)
    return out


class PNGDisplay:
    """Writes each frame to a PNG file for development on a desktop."""

    def __init__(self, path: str = "screen.png", scale: int = 2):
        self.width = WIDTH
        self.height = HEIGHT
        self.path = path
        self.scale = scale

    def show(self, image, full: bool = False, red_image=None):
        out = compose_red(image, red_image)
        if self.scale != 1:  # keep the image's own orientation
            out = out.resize((out.width * self.scale,
                              out.height * self.scale), resample=0)
        out.save(self.path)

    def clear(self, cycles: int = 1):
        self.show(Image.new("1", (self.width, self.height), 255))

    def sleep(self):
        pass

    def close(self):
        pass
