# Tinto 🍷

*Tinto* (Spanish for "red", one letter from *tinta* — ink): an EPUB
reader and widget device for the Waveshare 2.7" e-Paper HAT (264×176)
on a Raspberry Pi, with a desktop emulator for development. Supports
both the tri-color HAT (B) (black/white/red — the default, and the
namesake) and the plain black/white V2 panel (`--panel bw`).

Boots to a home menu with five apps:

- **E-Reader** — the EPUB reader (features below). Book pages render
  in landscape: rotate the device so the HAT buttons sit along the
  bottom edge, ordered KEY1..KEY4 left to right. (The library menu,
  the home menu and the other apps remain portrait.)
- **Widgets** — full-screen cards: clock (updates every minute),
  weather (Open-Meteo, no API key), and system info (IP, uptime, CPU
  and RAM usage, CPU temperature, disk). Flip between them with K1/K2;
  K4 returns home.
- **Manga** — an art frame of recent manga recommendations from the
  AniList API (free GraphQL, no key; one query returns titles, genres
  and covers together). The cover fills the whole screen with the
  title and genres overlaid at the bottom in red (on the tri-color
  panel; black on B/W), advancing every 5 minutes. Recommendations
  are fetched ~10 at a time into `manga_recs.json` with covers cached
  in `manga_cache/` (works offline on everything already fetched);
  when all stored items have been shown, the next batch is fetched
  and appended. The last shown manga persists in `reader_state.json`.
  Controls (not shown on screen): K1/K2 = previous/next manga
  (debounced; the 5-minute timer restarts on the chosen slide),
  K4 = home. A Jikan-era `manga_recs.json` is migrated automatically
  (reset and refetched on first run).

- **Wallpaper** — a landscape slideshow of your own images from the
  `wallpapers/` folder (created on first open, rescanned live), with
  a red clock overlaid bottom-center that updates every minute.
  Same dither pipeline as the manga covers, same controls (K1/K2 =
  previous/next, select/back = home), advancing on the shared slide
  interval. The last shown image persists in `reader_state.json`.
- **Settings** — device options, changed with the select/HOME button
  and persisted to `reader_state.json`: e-reader font size (12–22),
  the slide interval shared by the Manga and Wallpaper apps
  (3 / 5 / 10 minutes), the update check/trigger, and Reboot /
  Power off rows (press twice to confirm; they run
  `sudo -n systemctl reboot|poweroff`, so the user needs passwordless
  sudo — the Raspberry Pi OS default).

## Features

- Parses .epub files with the standard library only (no ebooklib needed)
- Word-wrapped, paginated text with first-line indents and bold headings
- Fast refresh on page turns, automatic full refresh every 12 pages to
  clear ghosting
- Bookmarks: remembers your position in every book and reopens the last
  book on startup
- Library menu listing everything in `books/`
- Four font sizes (16/18/20/22), cycled with a button
- Controllable from the HAT buttons or the keyboard (works over SSH)
- Clears the panel on startup and shutdown; deep-sleeps after 60 s idle
  to protect the display

## Desktop development (no hardware)

```bash
python3 -m venv .venv
.venv/bin/pip install Pillow
.venv/bin/python main.py --emulate
```

Drop .epub files into `books/`. The emulator uses the same keyboard
bindings as the hardware (see "Controls & actions" below), plus Esc
to quit.

`--png` mode renders each frame to `screen.png` instead of opening a
window (useful over SSH).

## Raspberry Pi setup

The Waveshare panel driver is vendored in `waveshare_epd/`, so no extra
repositories or `PYTHONPATH` setup are needed.

1. Enable SPI: `sudo raspi-config` → Interface Options → SPI → Yes
2. Install dependencies:
   ```bash
   sudo apt install python3-pil python3-gpiozero python3-spidev fonts-dejavu
   ```
3. Copy books into `books/` and run:
   ```bash
   python3 main.py              # tri-color HAT (B) — the default
   python3 main.py --panel bw   # plain black/white V2 panel
   ```

On the tri-color panel the reader drives the display in black/white
mode with fast refresh (~1 s page turns); the red plane is kept blank.
On the B/W panel page turns use partial refresh (~0.3 s, no flash).
Both panels get a full refresh every 12 page turns to clear ghosting.

`--start` boots directly into an app or widget instead of the home
menu — navigation is unchanged, K4 still gets you to the home menu:

```bash
python3 main.py --start reader    # resume the last book immediately
python3 main.py --start clock     # boot as a bedside clock
python3 main.py --start weather   # or: widgets, manga, settings, system
```

(Works with `--emulate` and `--png` too. Handy in the systemd unit:
`ExecStart=... main.py --start clock` turns the device into a clock
that still has everything else a button-press away.)

## Controls & actions

Three input sources produce the same events:

- **HAT keys** — the four keys on the panel (top to bottom in
  portrait; left to right under the screen in the reader's landscape).
- **Gesture buttons** (optional) — three external 3-pin button
  modules: VCC → 3.3 V, GND → GND, OUT → GPIO4 / GPIO27 / GPIO22.
  Press polarity is auto-detected at startup, so don't hold a button
  while the app boots. If the pins can't be claimed, the app runs
  with HAT keys only. (`test_buttons.py` is a standalone tester for
  them.)
- **Keyboard** — works whenever the app runs in a terminal (e.g. over
  SSH) and in the emulator; skipped automatically under systemd.

### Inputs → events

On-screen hints refer to the gesture buttons by name: **UP** (GPIO4),
**HOME** (GPIO27), **DOWN** (GPIO22) — e.g. "UP/DOWN · HOME=open",
"2×HOME=exit".

| Event | HAT key | Gesture button | Keyboard |
|---|---|---|---|
| up | KEY1 (GPIO5) | BTN1 push (GPIO4) | ↑ / ← / `p` |
| down | KEY2 (GPIO6) | BTN3 push (GPIO22) | ↓ / → / space / `n` |
| select | KEY3 (GPIO13) | BTN2 push (GPIO27) | Enter / `m` |
| back | KEY4 (GPIO19) | BTN2 long | Backspace / `f` |
| jump-back | — | BTN1 long | `[` |
| jump-forward | — | BTN3 long | `]` |
| alt-up | — | BTN1 double | `g` |
| alt-down | — | BTN3 double | `r` |
| home | — | BTN2 double | `h` |
| quit | — | via back at home | `q` (emulator: also Esc) |

Long push = held ≥ 0.8 s; double push = second press within 0.4 s.

### Actions per screen

| Event | Home menu | Reader: reading | Reader: library | Widgets | Manga | Wallpaper | Settings |
|---|---|---|---|---|---|---|---|
| up | selection up ° | previous page | selection up ° | previous widget | previous manga °† | previous image °† | selection up ° |
| down | selection down ° | next page | selection down ° | next widget | next manga °† | next image °† | selection down ° |
| select | open app | open library | open book / "< Home" | refresh widget | home | home | change value / "< Home" |
| back | **quit app** | open library | return to book | home | home | home | home |
| jump-back | first item ° | previous chapter | first item ° | — | — | — | first item ° |
| jump-forward | last item ° | next chapter | last item ° | — | — | — | last item ° |
| alt-up | — | cycle font size | — | — | — | — | — |
| alt-down | — | full refresh (deghost now) | — | — | fetch 10 more now | — | — |
| home | — | home menu | home menu | home menu | home menu | home menu | home menu |

° debounced: rapid presses move the selection silently; the screen
redraws once, half a second after the last press. select always acts
on the latest selection immediately.
† also restarts the slide timer ("Slide interval" in Settings); in
the manga app, next past the last stored item fetches a new batch
(wraps around when offline).

quit (keyboard `q`, or back on the home menu) clears the panel,
deep-sleeps it and exits. The library menu's last entry ("< Home")
also returns to the home menu.

### Automatic behaviors (no button involved)

- Reader: full refresh every 12 page turns to clear ghosting.
- All screens: panel deep-sleeps after 60 s idle; any input wakes it.
- Widgets: clock updates each minute, weather every 15 min (panel
  sleeps in between), system info each minute.
- Manga: slides advance on the Settings "Slide interval" (default
  5 min); new batch fetched and appended when all stored items have
  been shown; covers/genres self-heal when the network allows; cover
  cache pruned beyond 100 MB.
- Wallpaper: slides advance on the same interval; the folder is
  rescanned on every render, so images can be added/removed live.

### Weather widget location

Add your coordinates to `reader_state.json` (created on first run):

```json
"weather": {"latitude": 40.4168, "longitude": -3.7038, "name": "Madrid"}
```

### Stuck image?

E-paper keeps its last image with no power, so an interrupted demo or
crash leaves its picture on screen — that's normal and harmless. The
reader clears the panel itself on startup, but you can also wipe it
manually at any time:

```bash
python3 clear_screen.py
```

### Auto-update

No setup needed. Opening the Settings app checks GitHub for a newer
release in the background; when one exists, a row appears
("Update to vX") — select it and Tinto downloads the release tarball,
verifies it (byte-compiles every file, checks the bundled VERSION
against the tag), installs it and restarts.

The first update automatically creates a `releases/` directory next
to the code; from then on your original install directory acts as a
tiny launcher: it chain-loads the newest installed release, keeps all
data (books, bookmarks, calibration) where it always was, and if a
new version fails to boot it rolls back to the previous one on the
next start. The clone you installed from is never modified, so a
plain `git pull` there also still works.

The automatic restart after an update (and after a rollback) needs
systemd with `Restart=always` — see below. Without systemd the app
just exits after installing; start it again by hand.

### Run on boot (optional)

```ini
# /etc/systemd/system/tinto.service
[Unit]
Description=Tinto e-paper reader
After=multi-user.target

[Service]
User=pi
WorkingDirectory=/home/pi/e-reader
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now tinto.service
```

`Restart=always` makes self-update and rollback hands-free (the app
exits after installing and comes back up on the new version). It also
means the quit gesture restarts the app rather than stopping the
device — use `sudo systemctl stop tinto` for that.

## Project layout

```
main.py                entry point + self-update chain-loader
clear_screen.py        standalone panel wipe (incl. the red plane)
test_buttons.py        standalone gesture-button tester
reader/shell.py        home menu + event routing between apps
reader/app.py          ReaderApp: reading + library
reader/widgets_app.py  clock / weather / system info cards
reader/manga_app.py    AniList manga recommendations art frame
reader/wallpaper_app.py landscape slideshow of local images
reader/settings_app.py device options menu + update check/trigger
reader/updater.py      GitHub release check, A/B install, rollback prep
reader/epub.py         stdlib EPUB parser (zip + OPF + XHTML → text)
reader/layout.py       word wrap and pagination
reader/ui.py           renders pages/menus as 1-bit PIL images
reader/display.py      EPD driver wrapper (red-plane aware) + PNG backend
reader/buttons.py      HAT keys + gesture buttons (push/long/double)
reader/keyboard.py     terminal keyboard input (SSH)
reader/state.py        bookmarks + settings (reader_state.json)
reader/emulator.py     Tk desktop emulator
waveshare_epd/         vendored Waveshare panel driver (epd2in7_V2)
```

The driver files in `waveshare_epd/` are from
[waveshareteam/e-Paper](https://github.com/waveshareteam/e-Paper)
(MIT-style license, see file headers).
