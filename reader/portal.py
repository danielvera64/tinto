"""Web portal: file transfer + WiFi setup from any browser, no app.

A tiny stdlib HTTP server (default port 8080) with one mobile-friendly
page:

  Upload   drag/drop or pick files; .epub lands in books/, images in
           wallpapers/ (both apps pick new files up without a restart)
  WiFi     scan for networks, pick one, enter the password (via
           reader/netman.py; section shows "unavailable" without
           NetworkManager)

On the home network the portal is http://<device-ip>:8080. When the
device is offline it can host the Tinto-Setup hotspot (Settings, or
the automatic fallback watchdog) and the same page provisions the new
network at http://10.42.0.1:8080.

LAN-only by design: no authentication, so don't port-forward it.
"""

import email
import email.policy
import html
import io
import logging
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import netman

logger = logging.getLogger(__name__)

PORT = 8080
MAX_UPLOAD = 200 * 1024 * 1024  # bytes per request

BOOK_EXTS = (".epub",)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")

_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tinto</title><style>
body{{font-family:system-ui,sans-serif;margin:0 auto;max-width:34em;
     padding:1em;background:#faf7f2;color:#222}}
h1{{color:#8b1a1a}} h2{{border-bottom:2px solid #8b1a1a;padding-bottom:.2em}}
section{{background:#fff;border-radius:8px;padding:1em;margin:1em 0;
        box-shadow:0 1px 4px rgba(0,0,0,.1)}}
input,button{{font-size:1em;padding:.5em;margin:.3em 0}}
button{{background:#8b1a1a;color:#fff;border:0;border-radius:6px;
       padding:.6em 1.2em}}
.del{{background:#bbb;padding:.15em .6em;font-size:.85em}}
.filelist li{{margin:.25em 0}}
.filelist form{{display:inline;margin-left:.5em}}
.msg{{background:#e8f5e9;border-radius:6px;padding:.6em}}
.net{{display:block;margin:.35em 0}}
small{{color:#666}} ul{{margin:.3em 0;padding-left:1.2em}}
</style></head><body>
<h1>Tinto &#127863;</h1>
{msg}
<section><h2>Upload</h2>
<p>.epub &rarr; books &middot; images &rarr; wallpapers</p>
<form method="post" action="/upload" enctype="multipart/form-data">
<input type="file" name="files" multiple required>
<button>Upload</button></form>
<p><small>Books ({nbooks}): {books}</small></p>
<p><small>Wallpapers ({nwalls}):</small></p>
<ul class="filelist">{walls}</ul>
</section>
<section><h2>WiFi</h2>
{wifi}
</section>
</body></html>"""


def _fmt_names(names, limit=12):
    shown = ", ".join(html.escape(n) for n in names[:limit])
    if len(names) > limit:
        shown += f" &hellip; +{len(names) - limit} more"
    return shown or "&mdash;"


def _sanitize(filename):
    name = os.path.basename(filename.replace("\\", "/")).strip()
    name = name.lstrip(".")
    return name[:120]


def _parse_multipart(content_type, body):
    """Returns [(filename, bytes)] from a multipart/form-data body."""
    raw = (b"Content-Type: " + content_type.encode()
           + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    files = []
    if msg.is_multipart():
        for part in msg.iter_parts():
            filename = part.get_filename()
            if filename:
                payload = part.get_payload(decode=True)
                if payload:
                    files.append((filename, payload))
    return files


class PortalServer:
    def __init__(self, books_dir, wallpapers_dir, port=PORT):
        self.books_dir = books_dir
        self.wallpapers_dir = wallpapers_dir
        portal = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "TintoPortal/1"

            def log_message(self, *args):
                pass  # keep the console quiet

            # ---------------------------------------------------- pages

            def _send_html(self, body, code=200):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type",
                                 "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _redirect(self, location):
                self.send_response(303)
                self.send_header("Location", location)
                self.end_headers()

            def _page(self, msg="", networks=None):
                self._send_html(portal.render_page(msg, networks))

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                msg = query.get("msg", [""])[0]
                if parsed.path == "/" and "scan" in query:
                    self._page(msg, networks=portal.scan_networks())
                elif parsed.path == "/":
                    self._page(msg)
                else:
                    self._send_html("<h1>404</h1>", code=404)

            # --------------------------------------------------- actions

            def _read_body(self):
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0 or length > MAX_UPLOAD:
                    raise ValueError("upload too large or empty")
                return self.rfile.read(length)

            def do_POST(self):
                try:
                    if self.path == "/upload":
                        summary = portal.handle_upload(
                            self.headers.get("Content-Type", ""),
                            self._read_body())
                        self._redirect("/?msg="
                                       + urllib.parse.quote(summary))
                    elif self.path == "/delete":
                        form = urllib.parse.parse_qs(
                            self._read_body().decode())
                        result = portal.handle_delete(
                            form.get("file", [""])[0])
                        self._redirect("/?msg="
                                       + urllib.parse.quote(result))
                    elif self.path == "/wifi":
                        form = urllib.parse.parse_qs(
                            self._read_body().decode())
                        ssid = form.get("ssid", [""])[0].strip()
                        password = form.get("password", [""])[0]
                        self._page(portal.start_connect(ssid, password))
                    else:
                        self._send_html("<h1>404</h1>", code=404)
                except Exception as exc:
                    logger.warning("portal request failed: %s", exc)
                    self._send_html(
                        f"<h1>Error</h1><p>{html.escape(str(exc))}</p>"
                        '<p><a href="/">back</a></p>', code=400)

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.port = self.httpd.server_address[1]
        self._thread = None

    # ------------------------------------------------------------ logic

    def _listdir(self, path, exts):
        try:
            return sorted(f for f in os.listdir(path)
                          if f.lower().endswith(exts))
        except OSError:
            return []

    def render_page(self, msg="", networks=None):
        books = self._listdir(self.books_dir, BOOK_EXTS)
        walls = self._listdir(self.wallpapers_dir, IMAGE_EXTS)
        return _PAGE.format(
            msg=f'<p class="msg">{html.escape(msg)}</p>' if msg else "",
            nbooks=len(books), books=_fmt_names(books),
            nwalls=len(walls), walls=self._wallpaper_list(walls),
            wifi=self._wifi_section(networks))

    def _wallpaper_list(self, walls):
        if not walls:
            return "<li>&mdash;</li>"
        items = []
        for name in walls[:60]:
            esc = html.escape(name, quote=True)
            items.append(
                f"<li>{esc}"
                f'<form method="post" action="/delete" '
                f"onsubmit=\"return confirm('Delete {esc}?')\">"
                f'<input type="hidden" name="file" value="{esc}">'
                '<button class="del">delete</button></form></li>')
        if len(walls) > 60:
            items.append(f"<li>&hellip; +{len(walls) - 60} more</li>")
        return "".join(items)

    def handle_delete(self, name):
        """Deletes one wallpaper. Only image files directly inside the
        wallpapers dir can be removed (books are not deletable here)."""
        name = _sanitize(name)
        if not name.lower().endswith(IMAGE_EXTS):
            return "only wallpaper images can be deleted"
        path = os.path.join(self.wallpapers_dir, name)
        if os.path.dirname(os.path.abspath(path)) != \
                os.path.abspath(self.wallpapers_dir):
            return "invalid file name"
        try:
            os.remove(path)
        except FileNotFoundError:
            return f"{name} was already gone"
        except OSError as exc:
            return f"could not delete {name}: {exc}"
        logger.info("portal: deleted wallpaper %s", name)
        return f"deleted {name}"

    def _wifi_section(self, networks):
        if not netman.available():
            return "<p><small>NetworkManager not available on this " \
                   "system.</small></p>"
        status = ("setup hotspot active" if netman.hotspot_active()
                  else (netman.current_ssid() or "not connected"))
        out = [f"<p>Current: <b>{html.escape(status)}</b></p>"]
        if networks is None:
            out.append('<form method="get" action="/">'
                       '<input type="hidden" name="scan" value="1">'
                       "<button>Scan networks</button> "
                       "<small>takes a few seconds</small></form>")
        else:
            out.append('<form method="post" action="/wifi">')
            for n in networks[:15]:
                ssid = html.escape(n["ssid"])
                lock = "&#128274;" if n["secured"] else "open"
                out.append(
                    f'<label class="net"><input type="radio" name="ssid"'
                    f' value="{ssid}" required> {ssid} '
                    f"<small>({n['signal']}%, {lock})</small></label>")
            out.append('<input type="password" name="password" '
                       'placeholder="password (leave empty if open)">'
                       "<br><button>Connect</button></form>")
        return "".join(out)

    def scan_networks(self):
        try:
            return netman.scan()
        except Exception as exc:
            logger.warning("scan failed: %s", exc)
            return []

    def handle_upload(self, content_type, body):
        files = _parse_multipart(content_type, body)
        saved, skipped = [], []
        for filename, payload in files:
            name = _sanitize(filename)
            lower = name.lower()
            if lower.endswith(BOOK_EXTS):
                dest_dir = self.books_dir
            elif lower.endswith(IMAGE_EXTS):
                dest_dir = self.wallpapers_dir
            else:
                skipped.append(name)
                continue
            os.makedirs(dest_dir, exist_ok=True)
            with open(os.path.join(dest_dir, name), "wb") as f:
                f.write(payload)
            saved.append(name)
            logger.info("portal: saved %s (%d bytes)",
                        os.path.join(dest_dir, name), len(payload))
        parts = []
        if saved:
            parts.append(f"saved: {', '.join(saved)}")
        if skipped:
            parts.append(f"skipped (not epub/image): {', '.join(skipped)}")
        return "; ".join(parts) or "no files received"

    def start_connect(self, ssid, password):
        """Kicks off the WiFi join on a delayed thread (in hotspot mode
        the AP — and this HTTP connection — dies mid-switch, so the
        response must go out first) and returns the page saying so."""
        if not ssid:
            return self.render_page("choose a network first")

        def worker():
            import time
            time.sleep(1.5)  # let the response reach the browser
            ok, detail = netman.connect(ssid, password)
            logger.info("portal wifi join %s: ok=%s %s",
                        ssid, ok, detail)

        threading.Thread(target=worker, daemon=True).start()
        return self.render_page(
            f"Joining '{ssid}'… If this page was served by the setup "
            "hotspot, it will disappear now — reconnect your device to "
            "your normal WiFi. Tinto's new address appears in the "
            "System widget.")

    # ------------------------------------------------------------ life

    def start(self):
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info("portal listening on port %d", self.port)

    def stop(self):
        self.httpd.shutdown()
