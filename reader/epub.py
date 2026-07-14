"""Minimal EPUB parser using only the standard library.

An .epub file is a zip archive:
  META-INF/container.xml  -> points at the OPF package file
  <package>.opf           -> manifest (id -> href) and spine (reading order)
  content files (xhtml)   -> the actual chapters

We extract each spine document as a list of paragraphs of plain text.
"""

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List
from urllib.parse import unquote
from xml.etree import ElementTree as ET


@dataclass
class Paragraph:
    text: str
    kind: str = "p"  # "p" for body text, "h" for headings


@dataclass
class Chapter:
    title: str
    paragraphs: List[Paragraph] = field(default_factory=list)


@dataclass
class Book:
    path: str
    title: str
    author: str
    chapters: List[Chapter] = field(default_factory=list)


class _TextExtractor(HTMLParser):
    """Flattens XHTML into paragraphs, dropping tags/scripts/styles."""

    BLOCK_TAGS = {
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li",
        "blockquote", "pre", "td", "th", "section", "article",
        "figcaption", "dt", "dd",
    }
    HEADING_TAGS = {"h1", "h2", "h3", "h4"}
    SKIP_TAGS = {"script", "style", "head", "title", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs: List[Paragraph] = []
        self._buf: List[str] = []
        self._skip_depth = 0
        self._heading_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._flush()
            if tag in self.HEADING_TAGS:
                self._heading_depth += 1
        elif tag == "br":
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self._flush()
            if tag in self.HEADING_TAGS:
                self._heading_depth = max(0, self._heading_depth - 1)

    def handle_data(self, data):
        if not self._skip_depth:
            self._buf.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            kind = "h" if self._heading_depth else "p"
            self.paragraphs.append(Paragraph(text=text, kind=kind))

    def close(self):
        super().close()
        self._flush()


def _decode(raw: bytes) -> str:
    """Decodes a content document, honoring its declared encoding.

    Spanish/French/etc. epubs are frequently Latin-1 or Windows-1252
    rather than UTF-8; assuming UTF-8 turns every accented character
    into a replacement mark.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    # <?xml encoding="..."?> or <meta charset="..."> near the top
    m = re.search(rb'(?:encoding|charset)\s*=\s*["\']?([A-Za-z0-9_\-]+)',
                  raw[:1024])
    if m:
        try:
            return raw.decode(m.group(1).decode("ascii"), errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # cp1252 decodes any byte sequence and matches most Western-
        # European books that lack a declaration
        return raw.decode("cp1252", errors="replace")


def _findall(elem, tag):
    """Namespace-agnostic findall."""
    return elem.findall(".//{*}" + tag)


def _find(elem, tag):
    matches = _findall(elem, tag)
    return matches[0] if matches else None


def load_epub(path: str) -> Book:
    with zipfile.ZipFile(path) as zf:
        # 1. container.xml tells us where the OPF package file lives
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = _find(container, "rootfile")
        opf_path = rootfile.get("full-path")
        opf_dir = posixpath.dirname(opf_path)

        # 2. Parse the OPF: metadata, manifest, spine
        opf = ET.fromstring(zf.read(opf_path))

        title_el = _find(opf, "title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            title = posixpath.splitext(posixpath.basename(path))[0]
        author_el = _find(opf, "creator")
        author = (author_el.text or "").strip() if author_el is not None else ""

        manifest = {}
        for item in _findall(opf, "item"):
            manifest[item.get("id")] = {
                "href": unquote(item.get("href", "")),
                "type": item.get("media-type", ""),
            }

        spine_ids = [ref.get("idref") for ref in _findall(opf, "itemref")]

        # 3. Read each spine document in order and extract its text
        names = set(zf.namelist())
        chapters: List[Chapter] = []
        for idref in spine_ids:
            item = manifest.get(idref)
            if not item or "html" not in item["type"]:
                continue
            doc_path = posixpath.normpath(posixpath.join(opf_dir, item["href"]))
            if doc_path not in names:
                continue
            raw = _decode(zf.read(doc_path))
            extractor = _TextExtractor()
            extractor.feed(raw)
            extractor.close()
            paras = extractor.paragraphs
            if not paras:
                continue
            chap_title = next(
                (p.text for p in paras if p.kind == "h"),
                f"Section {len(chapters) + 1}",
            )
            chapters.append(Chapter(title=chap_title, paragraphs=paras))

    if not chapters:
        raise ValueError(f"No readable chapters found in {path}")
    return Book(path=path, title=title, author=author, chapters=chapters)
