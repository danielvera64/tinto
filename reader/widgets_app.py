"""Widgets app: full-screen cards (clock, weather, system info).

Controls:
  up / down  switch widget
  select     force-refresh the current widget
  back       return to the home menu

Each widget declares a render_key(); the shell's periodic tick
re-renders whenever the key changes (e.g. the clock's key is the
current minute). Slow widgets let the panel deep-sleep between
updates.
"""

import json
import logging
import os
import shutil
import socket
import time
import urllib.request

from PIL import Image, ImageDraw

from .layout import load_font
from .ui import BLACK, WHITE, MARGIN, FOOTER_HEIGHT

logger = logging.getLogger(__name__)

# WMO weather interpretation codes (Open-Meteo)
WMO_CODES = {
    0: "Clear sky", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Icy drizzle", 57: "Icy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Hailstorm",
}


def _fit_font(draw, text, max_width, sizes, bold=True):
    for size in sizes:
        font = load_font(size, bold=bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return load_font(sizes[-1], bold=bold)


def _center(draw, text, font, width, y, fill=BLACK):
    w = draw.textlength(text, font=font)
    draw.text(((width - w) // 2, y), text, font=font, fill=fill)


class Widget:
    name = "widget"
    update_interval = 60  # seconds; >= 300 lets the panel sleep between

    def render_key(self, now: float):
        """Re-render happens whenever this value changes."""
        return int(now // self.update_interval)

    def invalidate(self):
        pass

    def render(self, draw, width: int, height: int):
        raise NotImplementedError


class ClockWidget(Widget):
    name = "Clock"
    update_interval = 60

    def render_key(self, now):
        return time.strftime("%H:%M", time.localtime(now))

    def render(self, draw, width, height):
        now = time.localtime()
        t = time.strftime("%H:%M", now)
        font = _fit_font(draw, t, width - 16, [72, 64, 56, 48])
        _center(draw, t, font, width, int(height * 0.22))
        _center(draw, time.strftime("%A", now), load_font(17), width,
                int(height * 0.55))
        _center(draw, time.strftime("%d %B %Y", now), load_font(15), width,
                int(height * 0.55) + 24)


class WeatherWidget(Widget):
    """Current conditions from Open-Meteo (free, no API key).

    Configure the location in reader_state.json:
      "weather": {"latitude": 40.42, "longitude": -3.70, "name": "Madrid"}
    """

    name = "Weather"
    update_interval = 900  # 15 min; panel sleeps in between

    def __init__(self, state):
        self.state = state
        self._data = None
        self._fetched = 0.0
        self._error = None

    def _config(self):
        return self.state.data.get("weather") or {}

    def invalidate(self):
        self._fetched = 0.0

    def _ensure_data(self, now):
        cfg = self._config()
        if "latitude" not in cfg or "longitude" not in cfg:
            self._error = "no-config"
            return
        if self._data is not None and now - self._fetched < 870:
            return
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={cfg['latitude']}&longitude={cfg['longitude']}"
               "&current=temperature_2m,relative_humidity_2m,"
               "weather_code,wind_speed_10m"
               "&daily=temperature_2m_max,temperature_2m_min"
               "&timezone=auto&forecast_days=1")
        # Stamp before fetching so an offline device retries at the
        # normal cadence instead of hammering on every tick.
        self._fetched = now
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                raw = json.load(resp)
            self._data = {
                "temp": raw["current"]["temperature_2m"],
                "humidity": raw["current"]["relative_humidity_2m"],
                "wind": raw["current"]["wind_speed_10m"],
                "code": raw["current"]["weather_code"],
                "tmax": raw["daily"]["temperature_2m_max"][0],
                "tmin": raw["daily"]["temperature_2m_min"][0],
                "at": time.strftime("%H:%M"),
            }
            self._error = None
        except Exception as exc:
            logger.warning("weather fetch failed: %s", exc)
            self._error = "offline"

    def render(self, draw, width, height):
        self._ensure_data(time.time())
        cfg = self._config()
        title = cfg.get("name", "Weather")
        _center(draw, title, load_font(16, bold=True), width, MARGIN + 4)

        if self._error == "no-config":
            msg = ["No location set.", "Add to reader_state.json:",
                   '"weather": {', '  "latitude": 40.42,',
                   '  "longitude": -3.70,', '  "name": "Madrid"', "}"]
            font = load_font(12)
            y = int(height * 0.25)
            for line in msg:
                draw.text((MARGIN + 4, y), line, font=font, fill=BLACK)
                y += 17
            return

        if self._data is None:
            _center(draw, "No connection", load_font(14), width,
                    int(height * 0.4))
            return

        d = self._data
        temp = f"{round(d['temp'])}°"
        font = _fit_font(draw, temp, width - 20, [64, 56, 48])
        _center(draw, temp, font, width, int(height * 0.16))
        _center(draw, WMO_CODES.get(d["code"], "—"), load_font(15), width,
                int(height * 0.48))
        _center(draw, f"{round(d['tmin'])}° … {round(d['tmax'])}°",
                load_font(14), width, int(height * 0.58))
        _center(draw, f"{round(d['humidity'])}%  ·  {round(d['wind'])} km/h",
                load_font(13), width, int(height * 0.67))
        note = f"updated {d['at']}" + (" (offline)" if self._error else "")
        _center(draw, note, load_font(11), width, int(height * 0.76))


class SystemWidget(Widget):
    name = "System"
    update_interval = 60

    def __init__(self):
        self._prev_cpu = None  # (idle, total) from the previous render

    @staticmethod
    def _cpu_times():
        """Returns cumulative (idle, total) jiffies from /proc/stat."""
        with open("/proc/stat") as f:
            vals = [int(v) for v in f.readline().split()[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
        return idle, sum(vals)

    def _cpu_percent(self):
        try:
            now = self._cpu_times()
            if self._prev_cpu is None:
                # First render: measure over a short window
                time.sleep(0.25)
                self._prev_cpu, now = now, self._cpu_times()
            didle = now[0] - self._prev_cpu[0]
            dtotal = now[1] - self._prev_cpu[1]
            self._prev_cpu = now
            if dtotal <= 0:
                return "n/a"
            return f"{100 * (1 - didle / dtotal):.0f}%"
        except (OSError, ValueError, IndexError):
            # No /proc (e.g. macOS emulator): estimate from load average
            try:
                return f"~{min(100, os.getloadavg()[0] / os.cpu_count() * 100):.0f}%"
            except OSError:
                return "n/a"

    @staticmethod
    def _ram_percent():
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    key, _, rest = line.partition(":")
                    info[key] = int(rest.split()[0])
            used = 1 - info["MemAvailable"] / info["MemTotal"]
            return f"{100 * used:.0f}%"
        except (OSError, KeyError, ValueError, ZeroDivisionError):
            return "n/a"

    @staticmethod
    def _ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # no traffic sent; just picks a route
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "offline"

    @staticmethod
    def _uptime():
        try:
            with open("/proc/uptime") as f:
                secs = float(f.read().split()[0])
            return f"{int(secs // 86400)}d {int(secs % 86400 // 3600)}h " \
                   f"{int(secs % 3600 // 60)}m"
        except OSError:
            return "n/a"

    @staticmethod
    def _cpu_temp():
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return f"{int(f.read()) / 1000:.0f}°C"
        except (OSError, ValueError):
            return "n/a"

    def render(self, draw, width, height):
        _center(draw, "System", load_font(16, bold=True), width, MARGIN + 4)
        disk = shutil.disk_usage("/")
        rows = [
            ("host", socket.gethostname().split(".")[0]),
            ("ip", self._ip()),
            ("uptime", self._uptime()),
            ("cpu", self._cpu_percent()),
            ("ram", self._ram_percent()),
            ("cpu temp", self._cpu_temp()),
            ("disk free", f"{disk.free / 1e9:.1f} GB"),
            ("time", time.strftime("%H:%M")),
        ]
        label_font = load_font(12)
        value_font = load_font(13, bold=True)
        y = MARGIN + 34
        for label, value in rows:
            draw.text((MARGIN + 2, y), label, font=label_font, fill=BLACK)
            avail = (width - 2 * MARGIN - 10
                     - draw.textlength(label, font=label_font))
            while value and draw.textlength(value, font=value_font) > avail:
                value = value[:-1]
            w = draw.textlength(value, font=value_font)
            draw.text((width - MARGIN - 2 - w, y), value,
                      font=value_font, fill=BLACK)
            y += 24


class WidgetsApp:
    def __init__(self, display, state, on_home):
        self.display = display
        self.on_home = on_home
        self.widgets = [ClockWidget(), WeatherWidget(state), SystemWidget()]
        self.idx = 0
        self._last_key = None

    @property
    def widget(self):
        return self.widgets[self.idx]

    def activate(self):
        self._render(full=True)

    def handle(self, event):
        if event == "up":
            self.idx = (self.idx - 1) % len(self.widgets)
            self._render(full=True)
        elif event == "down":
            self.idx = (self.idx + 1) % len(self.widgets)
            self._render(full=True)
        elif event == "select":
            self.widget.invalidate()
            self._render()
        elif event == "back":
            self.on_home()

    def tick(self, now, idle_for):
        if self.widget.render_key(now) != self._last_key:
            self._render()

    def _render(self, full=False):
        img = Image.new("1", (self.display.width, self.display.height), WHITE)
        draw = ImageDraw.Draw(img)
        self.widget.render(draw, self.display.width, self.display.height)

        fy = self.display.height - FOOTER_HEIGHT
        draw.line((MARGIN, fy, self.display.width - MARGIN, fy), fill=BLACK)
        hint = (f"{self.widget.name} {self.idx + 1}/{len(self.widgets)}"
                " · 2×HOME=exit")
        font = load_font(11)
        w = draw.textlength(hint, font=font)
        draw.text(((self.display.width - w) // 2, fy + 2), hint,
                  font=font, fill=BLACK)

        self._last_key = self.widget.render_key(time.time())
        self.display.show(img, full=full)
        if self.widget.update_interval >= 300:
            self.display.sleep()  # slow widgets: sleep between updates
