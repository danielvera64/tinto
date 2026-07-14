"""ReaderApp: the e-book reading experience (one app under the Shell).

Events (from GPIO buttons, gesture buttons or the keyboard):
  "up"           previous page         / library: selection up
  "down"         next page             / library: selection down
  "select"       open the library menu / library: open selected item
  "back"         open the library menu / library: return to book
  "jump-back"    previous chapter      / library: first item
  "jump-forward" next chapter          / library: last item
  "alt-up"       cycle font size
  "alt-down"     manual full refresh (deghost the panel now)

The library menu's last entry ("< Home") exits to the shell's home
menu via the on_home callback.

Reading renders in LANDSCAPE (264x176): pages are composed wide and
the display layer rotates them onto the portrait panel (hold the
device with the buttons at the bottom, KEY1..KEY4 left to right).
The library menu and message screens stay PORTRAIT, like the rest of
the apps.
"""

import os
import time
from typing import List, Optional

from .epub import Book, load_epub
from .layout import Paginator
from .state import State
from .ui import Renderer, FOOTER_HEIGHT, MARGIN

HOME_ITEM = "< Home"
MENU_DEBOUNCE = 0.5  # settle time before a menu redraw, in seconds


class ReaderApp:
    def __init__(self, display, state: State, books_dir: str, on_home=None):
        self.display = display
        self.state = state
        self.books_dir = books_dir
        self.on_home = on_home or (lambda: None)

        self.mode = "menu"  # "read" | "menu"
        self.book: Optional[Book] = None
        self.chapter_idx = 0
        self.page_idx = 0
        self.menu_selection = 0
        self.render_due = None  # pending debounced menu redraw

        self._pages_cache = {}  # (chapter_idx, font_size) -> List[Page]
        self._build_layout_engine()

    def activate(self):
        """Called by the shell when the app is opened from the home menu:
        resume the last book if it still exists, else show the library."""
        self.render_due = None  # drop any stale pending redraw
        if self.state.font_size != self._layout_font:
            # font changed in the Settings app: rebuild the layout and
            # clamp the page index to the new pagination
            self._build_layout_engine()
            if self.book is not None:
                pages = self._chapter_pages(self.chapter_idx)
                self.page_idx = min(self.page_idx, len(pages) - 1)
        if self.book is not None:
            self.mode = "read"
            self.render_page(full=True)
            return
        last = self.state.data.get("last_book")
        if last and os.path.exists(last):
            self._open_book(last)
        else:
            self.show_menu()

    def tick(self, now, idle_for):
        """Debounced menu redraws + deep-sleep the panel when idle."""
        if (self.mode == "menu" and self.render_due is not None
                and now >= self.render_due):
            self.show_menu(full=False)  # fast refresh for navigation
        elif idle_for > 60:
            self.display.sleep()  # no-op if already asleep

    # ------------------------------------------------------------ layout

    def _build_layout_engine(self):
        size = self._layout_font = self.state.font_size
        # Menu/messages render portrait, like the rest of the apps.
        self.renderer = Renderer(self.display.width, self.display.height,
                                 size)
        # Reading renders landscape: pages are composed wide (264x176)
        # and the display layer rotates them onto the portrait panel.
        page_w = max(self.display.width, self.display.height)
        page_h = min(self.display.width, self.display.height)
        self.page_renderer = Renderer(page_w, page_h, size)
        self.paginator = Paginator(
            width=page_w - 2 * MARGIN,
            height=page_h - 2 * MARGIN - FOOTER_HEIGHT,
            font_size=size,
        )
        self._pages_cache = {}

    def _chapter_pages(self, idx: int):
        key = (idx, self.state.font_size)
        if key not in self._pages_cache:
            self._pages_cache[key] = self.paginator.paginate(
                self.book.chapters[idx].paragraphs)
        return self._pages_cache[key]

    # ------------------------------------------------------------ library

    def list_books(self) -> List[str]:
        try:
            files = sorted(
                f for f in os.listdir(self.books_dir)
                if f.lower().endswith(".epub")
            )
        except OSError:
            files = []
        return [os.path.join(self.books_dir, f) for f in files]

    def show_menu(self, full: bool = True):
        self.mode = "menu"
        self.render_due = None
        books = self.list_books()
        labels = [os.path.splitext(os.path.basename(b))[0] for b in books]
        labels.append(HOME_ITEM)
        self.menu_selection = min(self.menu_selection, len(labels) - 1)
        hint = ("copy .epub files into books/" if not books
                else "UP/DOWN · HOME=open")
        img = self.renderer.render_menu(
            "Library", labels, self.menu_selection, hint=hint)
        self.display.show(img, full=full)

    def _open_book(self, path: str):
        try:
            self.book = load_epub(path)
        except Exception as exc:  # corrupt/unsupported epub
            self.display.show(
                self.renderer.render_message(f"Could not open book: {exc}"),
                full=True)
            self.mode = "menu"
            return
        # The pagination cache is keyed by (chapter, font_size) only --
        # stale entries from the previous book must not survive here.
        self._pages_cache = {}
        mark = self.state.bookmark(path)
        self.chapter_idx = min(mark["chapter"], len(self.book.chapters) - 1)
        pages = self._chapter_pages(self.chapter_idx)
        self.page_idx = min(mark["page"], len(pages) - 1)
        self.mode = "read"
        self.render_page(full=True)

    # ------------------------------------------------------------ reading

    def render_page(self, full: bool = False):
        pages = self._chapter_pages(self.chapter_idx)
        page = pages[self.page_idx]
        footer = (f"{self.chapter_idx + 1}/{len(self.book.chapters)} · "
                  f"{self.page_idx + 1}/{len(pages)}")
        img = self.page_renderer.render_page(
            page.lines, self.paginator.line_height,
            self.paginator.font, self.paginator.bold_font, footer)
        self.display.show(img, full=full)
        self.state.set_bookmark(self.book.path, self.chapter_idx, self.page_idx)

    def next_page(self):
        pages = self._chapter_pages(self.chapter_idx)
        if self.page_idx + 1 < len(pages):
            self.page_idx += 1
        elif self.chapter_idx + 1 < len(self.book.chapters):
            self.chapter_idx += 1
            self.page_idx = 0
        else:
            return  # end of book
        self.render_page()

    def prev_page(self):
        if self.page_idx > 0:
            self.page_idx -= 1
        elif self.chapter_idx > 0:
            self.chapter_idx -= 1
            self.page_idx = len(self._chapter_pages(self.chapter_idx)) - 1
        else:
            return  # start of book
        self.render_page()

    def prev_chapter(self):
        if self.chapter_idx > 0:
            self.chapter_idx -= 1
        self.page_idx = 0
        self.render_page()

    def next_chapter(self):
        if self.chapter_idx + 1 < len(self.book.chapters):
            self.chapter_idx += 1
            self.page_idx = 0
            self.render_page()

    def cycle_font(self):
        self.state.cycle_font_size()
        self._build_layout_engine()
        if self.mode == "read" and self.book:
            # Page numbers shift with font size; clamp to the new count
            pages = self._chapter_pages(self.chapter_idx)
            self.page_idx = min(self.page_idx, len(pages) - 1)
            self.render_page(full=True)
        else:
            self.show_menu()

    # ------------------------------------------------------------ events

    def handle(self, event: str):
        if self.mode == "read":
            if event == "up":
                self.prev_page()
            elif event == "down":
                self.next_page()
            elif event in ("select", "back"):
                self.show_menu()
            elif event == "jump-back":
                self.prev_chapter()
            elif event == "jump-forward":
                self.next_chapter()
            elif event == "alt-up":
                self.cycle_font()
            elif event == "alt-down":
                self.render_page(full=True)  # manual deghost
        elif self.mode == "menu":
            books = self.list_books()
            count = len(books) + 1  # + Home entry
            # Navigation is debounced: presses move the selection
            # immediately, the screen redraws once after MENU_DEBOUNCE
            # without keystrokes (see tick()).
            if event == "up":
                self.menu_selection = (self.menu_selection - 1) % count
                self.render_due = time.time() + MENU_DEBOUNCE
            elif event == "down":
                self.menu_selection = (self.menu_selection + 1) % count
                self.render_due = time.time() + MENU_DEBOUNCE
            elif event == "jump-back":
                self.menu_selection = 0
                self.render_due = time.time() + MENU_DEBOUNCE
            elif event == "jump-forward":
                self.menu_selection = count - 1
                self.render_due = time.time() + MENU_DEBOUNCE
            elif event == "select":
                self.render_due = None  # acts on the latest selection
                if self.menu_selection >= len(books):
                    self.on_home()
                else:
                    self._open_book(books[self.menu_selection])
            elif event == "back" and self.book:
                self.render_due = None
                self.mode = "read"
                self.render_page(full=True)
