"""Renders reading pages and menu screens as 1-bit PIL images.

The panel is used in portrait orientation: 176 wide x 264 tall.
"""

from typing import List

from PIL import Image, ImageDraw

from .layout import Line, load_font

BLACK = 0
WHITE = 255

MARGIN = 6
FOOTER_HEIGHT = 16


class Renderer:
    def __init__(self, width: int, height: int, font_size: int):
        self.width = width
        self.height = height
        self.font_size = font_size
        self.small_font = load_font(11)
        self.title_font = load_font(14, bold=True)

    @property
    def text_width(self) -> int:
        return self.width - 2 * MARGIN

    @property
    def text_height(self) -> int:
        return self.height - 2 * MARGIN - FOOTER_HEIGHT

    def _blank(self) -> Image.Image:
        return Image.new("1", (self.width, self.height), WHITE)

    def render_page(self, lines: List[Line], line_height: int,
                    body_font, bold_font, footer: str) -> Image.Image:
        img = self._blank()
        draw = ImageDraw.Draw(img)
        y = MARGIN
        for line in lines:
            font = bold_font if line.bold else body_font
            draw.text((MARGIN + line.indent, y), line.text, font=font, fill=BLACK)
            y += line_height

        # Footer: separator + centered progress text
        fy = self.height - FOOTER_HEIGHT
        draw.line((MARGIN, fy, self.width - MARGIN, fy), fill=BLACK)
        w = draw.textlength(footer, font=self.small_font)
        draw.text(((self.width - w) // 2, fy + 2), footer,
                  font=self.small_font, fill=BLACK)
        return img

    def render_menu(self, title: str, items: List[str], selected: int,
                    hint: str = "") -> Image.Image:
        img = self._blank()
        draw = ImageDraw.Draw(img)

        draw.text((MARGIN, MARGIN), title, font=self.title_font, fill=BLACK)
        y = MARGIN + 22
        draw.line((MARGIN, y - 4, self.width - MARGIN, y - 4), fill=BLACK)

        row_h = 20
        visible = max(1, (self.height - y - FOOTER_HEIGHT) // row_h)
        top = max(0, min(selected - visible // 2, len(items) - visible))
        for i in range(top, min(top + visible, len(items))):
            label = items[i]
            # Truncate long labels to fit
            while label and draw.textlength("> " + label, font=self.small_font) > self.text_width:
                label = label[:-1]
            prefix = "> " if i == selected else "  "
            if i == selected:
                draw.rectangle((MARGIN - 2, y - 1,
                                self.width - MARGIN + 2, y + row_h - 4),
                               fill=BLACK)
                draw.text((MARGIN, y), prefix + label,
                          font=self.small_font, fill=WHITE)
            else:
                draw.text((MARGIN, y), prefix + label,
                          font=self.small_font, fill=BLACK)
            y += row_h

        if hint:
            fy = self.height - FOOTER_HEIGHT
            draw.line((MARGIN, fy, self.width - MARGIN, fy), fill=BLACK)
            w = draw.textlength(hint, font=self.small_font)
            draw.text(((self.width - w) // 2, fy + 2), hint,
                      font=self.small_font, fill=BLACK)
        return img

    def render_message(self, text: str) -> Image.Image:
        img = self._blank()
        draw = ImageDraw.Draw(img)
        words = text.split()
        lines, current = [], []
        for word in words:
            if draw.textlength(" ".join(current + [word]),
                               font=self.title_font) > self.text_width:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))
        total_h = len(lines) * 20
        y = (self.height - total_h) // 2
        for line in lines:
            w = draw.textlength(line, font=self.title_font)
            draw.text(((self.width - w) // 2, y), line,
                      font=self.title_font, fill=BLACK)
            y += 20
        return img
