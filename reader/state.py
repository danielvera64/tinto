"""Persistent reader state: last open book, per-book bookmarks, font size."""

import json
import os

DEFAULT_FONT_SIZE = 16
FONT_SIZES = [12, 14, 16, 18, 20, 22]


class State:
    def __init__(self, path: str):
        self.path = path
        self.data = {
            "last_book": None,
            "font_size": DEFAULT_FONT_SIZE,
            "books": {},  # book path -> {"chapter": int, "page": int}
        }
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    @property
    def font_size(self) -> int:
        return self.data.get("font_size", DEFAULT_FONT_SIZE)

    def cycle_font_size(self) -> int:
        sizes = FONT_SIZES
        current = self.font_size
        idx = (sizes.index(current) + 1) % len(sizes) if current in sizes else 0
        self.data["font_size"] = sizes[idx]
        self.save()
        return sizes[idx]

    def bookmark(self, book_path: str):
        return self.data["books"].get(book_path, {"chapter": 0, "page": 0})

    def set_bookmark(self, book_path: str, chapter: int, page: int):
        self.data["books"][book_path] = {"chapter": chapter, "page": page}
        self.data["last_book"] = book_path
        self.save()
