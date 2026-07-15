"""Manga app: an e-paper art frame of recent manga recommendations.

Data comes from the AniList GraphQL API (free, no key needed):
https://graphql.anilist.co — one query returns recommendation, title,
genres and cover URL together, so there are no per-title follow-up
calls. (The previous Jikan backend needed 1 + 10 requests per batch,
required curl as transport because api.jikan.moe rejects Python's TLS
fingerprint, and 504'd on titles missing from its cache.)

AniList's recent-recommendations feed mixes anime and manga; only
manga are kept, so filling a batch of BATCH items may consume a few
feed pages (the page cursor persists in manga_recs.json). Covers are
downloaded to manga_cache/ as screen-sized grayscale PNGs, so
everything already fetched keeps working offline. The slide advances
every CHANGE_EVERY seconds; the last shown position persists in
reader_state.json ("manga" key). When every stored recommendation has
been shown, the next batch is fetched and appended; offline the app
wraps around instead.

The cover fills the whole screen with the title and genres overlaid
near the bottom in red (the tri-color panel's red plane; black on B/W
panels). No footer or key hints are drawn. Controls: up/down show the
previous/next manga (debounced like the menus, and the slide timer
restarts on the chosen one); select and back both return to the home
menu (the HOME button exits on a single push).
"""

import io
import json
import logging
import os
import shutil
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .layout import load_font
from .ui import BLACK, WHITE

logger = logging.getLogger(__name__)

ANILIST_URL = "https://graphql.anilist.co"
QUERY = """
query ($page: Int) {
  Page(page: $page, perPage: 25) {
    pageInfo { hasNextPage }
    recommendations(sort: ID_DESC) {
      media {
        id type isAdult
        title { romaji english }
        genres
        coverImage { large }
      }
    }
  }
}"""

HEADERS = {"User-Agent": "epaper-ereader/1.0 (personal device)"}
BATCH = 10          # recommendations fetched per batch
CHANGE_EVERY = 300  # default seconds between slides (Settings overrides)
API_PACE = 0.7      # s between feed pages (AniList limit: 90 req/min)
NAV_DEBOUNCE = 0.5  # settle time after prev/next presses, in seconds
MAX_PAGES_PER_BATCH = 6  # feed pages to scan for BATCH manga
MAX_CACHE_BYTES = 100 * 1024 * 1024  # prune manga_cache/ beyond this
CACHE_PRUNE_TARGET = 0.8  # prune down to this fraction of the limit


class MangaApp:
    def __init__(self, display, state, data_dir, on_home):
        self.display = display
        self.state = state
        self.on_home = on_home
        self.data_path = os.path.join(data_dir, "manga_recs.json")
        self.cache_dir = os.path.join(data_dir, "manga_cache")
        # page = next unread page of AniList's recommendations feed.
        # version 2: adult titles are filtered out at fetch time.
        self.data = {"source": "anilist", "version": 2,
                     "page": 1, "items": []}
        self._load()
        self._last_change = 0.0
        self.render_due = None  # pending debounced redraw (shell polls this)
        self._cover_retry_failed = set()  # ids not to retry this session
        self._prune_cache()

    # ------------------------------------------------------------ storage

    def _load(self):
        try:
            with open(self.data_path) as f:
                loaded = json.load(f)
        except (OSError, ValueError):
            return
        if (loaded.get("source") == "anilist"
                and loaded.get("version") == self.data["version"]):
            self.data.update(loaded)
        else:
            # Older store: either Jikan-era (MAL ids collide with
            # AniList ids) or fetched before adult filtering existed,
            # so its items cannot be trusted. Start fresh -- a batch
            # regenerates in one fetch.
            logger.info("migrating manga store (fresh start)")
            shutil.rmtree(self.cache_dir, ignore_errors=True)

    def _save(self):
        tmp = self.data_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.data_path)

    def _prune_cache(self):
        """Keeps manga_cache/ under MAX_CACHE_BYTES: deletes orphaned
        files first (covers no stored item references), then the oldest
        covers. A pruned cover that is still needed re-downloads itself
        the next time its slide renders (see _draw_cover)."""
        try:
            entries = []
            for e in os.scandir(self.cache_dir):
                if e.is_file():
                    st = e.stat()
                    entries.append((e.path, st.st_mtime, st.st_size))
        except OSError:
            return  # no cache dir yet
        total = sum(size for _, _, size in entries)
        if total <= MAX_CACHE_BYTES:
            return
        referenced = {item.get("image_file") for item in self.data["items"]}
        # sort: orphans first (False < True), then oldest first
        entries.sort(key=lambda t: (os.path.basename(t[0]) in referenced,
                                    t[1]))
        target = MAX_CACHE_BYTES * CACHE_PRUNE_TARGET
        removed = 0
        for path, _, size in entries:
            if total <= target:
                break
            try:
                os.remove(path)
                total -= size
                removed += 1
            except OSError:
                pass
        logger.info("manga cache exceeded %d MB: pruned %d covers "
                    "(now %.1f MB)", MAX_CACHE_BYTES // 2**20, removed,
                    total / 2**20)

    @property
    def index(self) -> int:
        return self.state.data.get("manga", {}).get("index", 0)

    def _set_index(self, i: int):
        self.state.data["manga"] = {"index": i}
        self.state.save()

    # ------------------------------------------------------------ fetching

    def _fetch_recs(self, page: int):
        """One feed page -> (list of manga media dicts, has_next_page)."""
        body = json.dumps({"query": QUERY,
                           "variables": {"page": page}}).encode()
        req = urllib.request.Request(
            ANILIST_URL, data=body,
            headers={**HEADERS, "Content-Type": "application/json"})
        last_exc = None
        for attempt in range(2):
            if attempt:
                time.sleep(2)
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    payload = json.load(resp)
                break
            except Exception as exc:
                last_exc = exc
        else:
            raise last_exc
        page_data = payload["data"]["Page"]
        manga = [r["media"] for r in page_data["recommendations"]
                 if r.get("media")
                 and r["media"]["type"] == "MANGA"
                 and not r["media"].get("isAdult")          # no hentai
                 and "Hentai" not in (r["media"].get("genres") or [])]
        return manga, page_data["pageInfo"]["hasNextPage"]

    def _cover_size(self):
        return (self.display.width, self.display.height)  # full screen

    def _download_image(self, media_id, url, force=False):
        """Stores the cover as a grayscale PNG at the exact screen size
        (Lanczos resample — upscaling later would blur). Returns the
        cache filename, or None if the download failed."""
        if not url:
            return None
        fname = f"{media_id}.png"
        path = os.path.join(self.cache_dir, fname)
        if os.path.exists(path) and not force:
            return fname
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
            img = Image.open(io.BytesIO(raw)).convert("L")
            img = ImageOps.fit(img, self._cover_size(), Image.LANCZOS)
            os.makedirs(self.cache_dir, exist_ok=True)
            img.save(path)
            return fname
        except Exception as exc:
            logger.warning("cover download failed for %s: %s",
                           media_id, exc)
            return None

    def fetch_more(self) -> int:
        """Appends up to BATCH new manga from the recommendations feed.
        Returns how many were added (0 when offline)."""
        known = {item["id"] for item in self.data["items"]}
        added = 0
        for _ in range(MAX_PAGES_PER_BATCH):
            if added >= BATCH:
                break
            try:
                manga, has_next = self._fetch_recs(self.data["page"])
            except Exception as exc:
                logger.warning("recommendation fetch failed: %s", exc)
                break
            for m in manga:
                if m["id"] in known:
                    continue
                known.add(m["id"])
                title = (m["title"].get("english")
                         or m["title"].get("romaji") or "Untitled")
                url = (m.get("coverImage") or {}).get("large")
                self.data["items"].append({
                    "id": m["id"],
                    "title": title,
                    "genres": (m.get("genres") or [])[:4],
                    "image_url": url,
                    "image_file": self._download_image(m["id"], url),
                })
                added += 1
            if has_next:
                self.data["page"] += 1
            else:
                self.data["page"] = 1  # feed exhausted; newest next time
                break
            time.sleep(API_PACE)
        self._save()  # items and/or cursor moved
        self._prune_cache()  # downloads may have pushed it past the cap
        return added

    # ------------------------------------------------------------ app API

    def activate(self):
        self.render_due = None  # drop any stale pending redraw
        if not self.data["items"]:
            self.fetch_more()
        if self.data["items"]:
            self._set_index(min(self.index, len(self.data["items"]) - 1))
        self._last_change = time.time()
        self._show(full=True)

    def handle(self, event):
        if event in ("select", "back"):
            # The manga frame has no select action, so the HOME button
            # (push = select) exits to the home menu -- a dead single
            # press on the button named HOME reads as a broken button.
            self.render_due = None
            self.on_home()
            return
        if event == "alt-down":
            self.fetch_more()  # extend the pool now (silent)
            return
        items = self.data["items"]
        if not items or event not in ("up", "down"):
            return
        # Debounced navigation: presses move the index immediately, the
        # screen redraws once NAV_DEBOUNCE after the last press.
        if event == "up":
            self._set_index((self.index - 1) % len(items))
        else:
            nxt = self.index + 1
            if nxt >= len(items):        # past the end: extend or wrap
                self.fetch_more()
                if nxt >= len(self.data["items"]):
                    nxt = 0
            self._set_index(nxt)
        self._last_change = time.time()  # chosen slide gets a full stay
        self.render_due = time.time() + NAV_DEBOUNCE

    def _change_every(self) -> int:
        """Seconds between slides; configurable in the Settings app."""
        return self.state.data.get("manga_refresh_min",
                                   CHANGE_EVERY // 60) * 60

    def tick(self, now, idle_for):
        if self.render_due is not None and now >= self.render_due:
            self.render_due = None
            self._show()
            return
        if not self.data["items"]:
            return
        if now - self._last_change >= self._change_every():
            self._advance()
        elif idle_for > 60 and self.render_due is None:
            self.display.sleep()  # no-op if already asleep

    def _advance(self):
        items = self.data["items"]
        nxt = self.index + 1
        if nxt >= len(items):
            if self.fetch_more() == 0 and not items:
                self._show()  # still empty: render the offline message
                return
            items = self.data["items"]
            if nxt >= len(items):
                nxt = 0  # offline: wrap around the stored batch
        self._set_index(nxt)
        self._last_change = time.time()
        self._show()

    # ------------------------------------------------------------ drawing

    def _show(self, full=True):
        # Always a full refresh: slides change every 5 minutes, and the
        # full waveform gives deeper blacks and no ghosting -- fast
        # refresh visibly muddies dithered artwork.
        W, H = self.display.width, self.display.height
        img = Image.new("1", (W, H), WHITE)
        draw = ImageDraw.Draw(img)
        items = self.data["items"]

        if not items:
            font = load_font(14, bold=True)
            for i, line in enumerate(["No recommendations", "yet — is the",
                                      "network up?"]):
                w = draw.textlength(line, font=font)
                draw.text(((W - w) // 2, H // 3 + i * 20), line,
                          font=font, fill=BLACK)
            self.display.show(img, full=full)
            return

        item = items[self.index % len(items)]
        self._draw_cover(img, draw, item)
        red = Image.new("1", (W, H), WHITE)  # red overlay plane
        self._draw_title(img, red, item["title"], W, H,
                         genres=item.get("genres") or [])
        self.display.show(img, full=full, red_image=red)

    def _draw_cover(self, img, draw, item):
        W, H = self._cover_size()
        cover = None
        if item.get("image_file"):
            try:
                cover = Image.open(
                    os.path.join(self.cache_dir, item["image_file"]))
            except OSError:
                cover = None
        if cover is not None and cover.size != (W, H):
            # Stale cache from an older layout: stored smaller than the
            # screen, so it would upscale blurry. Re-download once at
            # full quality; keep the old file if offline.
            fname = self._download_image(item["id"],
                                         item.get("image_url"), force=True)
            if fname:
                cover = Image.open(os.path.join(self.cache_dir, fname))
        elif (cover is None and item.get("image_url")
                and item["id"] not in self._cover_retry_failed):
            # Cover missing (download failed at fetch time): retry once
            # per session so it heals itself when the network allows.
            fname = self._download_image(item["id"], item["image_url"])
            if fname:
                item["image_file"] = fname
                self._save()
                cover = Image.open(os.path.join(self.cache_dir, fname))
            else:
                self._cover_retry_failed.add(item["id"])
        if cover is None:
            draw.rectangle((2, 2, W - 3, H - 3), outline=BLACK)
            font = load_font(12)
            w = draw.textlength("no image", font=font)
            draw.text(((W - w) // 2, H // 2), "no image",
                      font=font, fill=BLACK)
            return
        cover = cover.convert("L")
        if cover.size != (W, H):  # offline fallback: old cache upscaled
            cover = ImageOps.fit(cover, (W, H), Image.LANCZOS)
        # Contrast stretch + unsharp mask before dithering: 1-bit
        # Floyd-Steinberg keeps edges only if they are strong going in.
        cover = ImageOps.autocontrast(cover, cutoff=2)
        cover = cover.filter(ImageFilter.UnsharpMask(
            radius=2, percent=180, threshold=2))
        img.paste(cover.convert("1"), (0, 0))  # Floyd-Steinberg dithering

    def _draw_title(self, img, red, title, W, H, genres=()):
        """Overlays the title (and a small genre line) near the bottom:
        red strokes (red plane) over a white halo punched into the
        cover so they stay readable."""
        font = load_font(11, bold=True)
        max_w = W - 8
        base_draw = ImageDraw.Draw(img)
        red_draw = ImageDraw.Draw(red)

        # wrap to at most 2 lines, ellipsize overflow
        words = title.split()
        lines, current = [], []
        for word in words:
            if base_draw.textlength(" ".join(current + [word]),
                                    font=font) > max_w:
                lines.append(" ".join(current))
                current = [word]
                if len(lines) == 2:
                    break
            else:
                current.append(word)
        if current and len(lines) < 2:
            lines.append(" ".join(current))
        elif current:
            lines[-1] = lines[-1] + "…"
        while lines and base_draw.textlength(lines[-1], font=font) > max_w:
            lines[-1] = lines[-1][:-2] + "…"

        genre_font = load_font(9)
        genre_line = " · ".join(genres)
        while (genre_line
               and base_draw.textlength(genre_line, font=genre_font) > max_w):
            genre_line = genre_line.rsplit(" · ", 1)[0]

        line_h = 16
        y = H - len(lines) * line_h - (14 if genre_line else 0) - 6
        for line in lines:
            w = base_draw.textlength(line, font=font)
            x = (W - w) // 2
            # white halo in the black plane, red strokes in the red plane
            base_draw.text((x, y), line, font=font, fill=WHITE,
                           stroke_width=2, stroke_fill=WHITE)
            red_draw.text((x, y), line, font=font, fill=BLACK)
            y += line_h
        if genre_line:
            w = base_draw.textlength(genre_line, font=genre_font)
            x = (W - w) // 2
            base_draw.text((x, y + 1), genre_line, font=genre_font,
                           fill=WHITE, stroke_width=2, stroke_fill=WHITE)
            red_draw.text((x, y + 1), genre_line, font=genre_font,
                          fill=BLACK)
