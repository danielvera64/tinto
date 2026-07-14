# Tinto

E-paper reader, widgets and manga frame for the Waveshare 2.7" HAT on
a Raspberry Pi (repo: github.com/danielvera64/tinto).

## Releases

When creating a new release (git tag / GitHub release), the version
MUST use the timestamp format:

    vYYYY.MM.DD-HH.MM

e.g. `v2026.07.14-16.30` — the current date and time (24 h), zero
padded. No semver.

The release TITLE must be exactly the version string (e.g.
`v2026.07.14-16.30`) — do not prefix it with the app name.

Before tagging, update the `VERSION` file in the project root to the
new version string and commit it — the Settings app displays it, and
the update checker compares it against the latest GitHub release.

## Development notes

- Test on macOS with the emulator: `.venv/bin/python main.py --emulate`
  (or `--png` for headless). Hardware-only code paths (waveshare_epd,
  gpiozero) cannot run on the Mac.
- Runtime state lives in `reader_state.json` and `manga_recs.json` /
  `manga_cache/` — per-device, gitignored, never commit them.
- `books/` is gitignored except the public-domain Alice in Wonderland
  sample; never commit copyrighted epubs.
