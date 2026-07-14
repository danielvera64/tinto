"""Text layout: wraps chapter paragraphs into fixed-size e-paper pages."""

import logging
import os
from dataclasses import dataclass, field
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Candidate body fonts, in preference order. All of these have full
# Latin coverage (accents, ñ, ¿ ¡, …). DejaVu/Liberation/FreeSerif
# cover Raspberry Pi OS; the /System ones cover macOS for the emulator.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_warned_fallback = False


def _first_existing(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _first_existing(BOLD_CANDIDATES if bold else FONT_CANDIDATES)
    if path:
        return ImageFont.truetype(path, size)
    global _warned_fallback
    if not _warned_fallback:
        _warned_fallback = True
        logger.warning(
            "No TrueType font found — falling back to PIL's built-in "
            "bitmap font, which is ASCII-ONLY (accented characters will "
            "render as boxes). Install one with: sudo apt install fonts-dejavu")
    return ImageFont.load_default()


@dataclass
class Line:
    text: str
    bold: bool = False
    indent: int = 0


@dataclass
class Page:
    lines: List[Line] = field(default_factory=list)


class Paginator:
    """Turns a chapter's paragraphs into pages for a given viewport."""

    PARA_INDENT = 12  # first-line indent for body paragraphs, in px

    def __init__(self, width: int, height: int, font_size: int,
                 line_spacing: int = 3, para_spacing: int = 4):
        self.width = width
        self.height = height
        self.font = load_font(font_size)
        self.bold_font = load_font(font_size, bold=True)
        ascent, descent = self.font.getmetrics()
        self.line_height = ascent + descent + line_spacing
        self.para_spacing = para_spacing
        self._measure = ImageDraw.Draw(Image.new("1", (1, 1)))

    def _text_width(self, text: str, bold: bool) -> float:
        return self._measure.textlength(
            text, font=self.bold_font if bold else self.font)

    def _wrap(self, text: str, bold: bool, first_indent: int) -> List[Tuple[str, int]]:
        """Greedy word wrap. Returns (line_text, indent) tuples."""
        lines: List[Tuple[str, int]] = []
        words = text.split()
        current: List[str] = []
        indent = first_indent
        avail = self.width - indent

        def flush():
            nonlocal current, indent, avail
            if current:
                lines.append((" ".join(current), indent))
            current = []
            indent = 0
            avail = self.width

        for word in words:
            candidate = " ".join(current + [word])
            if self._text_width(candidate, bold) <= avail:
                current.append(word)
                continue
            flush()
            # Hard-break words wider than a full line
            while self._text_width(word, bold) > self.width:
                cut = len(word)
                while cut > 1 and self._text_width(word[:cut], bold) > self.width:
                    cut -= 1
                lines.append((word[:cut], 0))
                word = word[cut:]
            current = [word]
        flush()
        return lines

    def paginate(self, paragraphs) -> List[Page]:
        pages: List[Page] = [Page()]
        y = 0
        for para in paragraphs:
            bold = para.kind == "h"
            indent = 0 if bold else self.PARA_INDENT
            for text, ind in self._wrap(para.text, bold, indent):
                if y + self.line_height > self.height:
                    pages.append(Page())
                    y = 0
                pages[-1].lines.append(Line(text=text, bold=bold, indent=ind))
                y += self.line_height
            y += self.para_spacing
        if not pages[-1].lines and len(pages) > 1:
            pages.pop()
        return pages
