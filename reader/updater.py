"""Self-update from GitHub releases (A/B directory scheme).

Managed layout on the device (created once, see README):

    <root>/run.sh                     launcher with rollback (from repo)
    <root>/current  -> releases/<v>   symlink deciding what runs
    <root>/previous -> releases/<v>   rollback target (set on update)
    <root>/releases/<version>/        one directory per installed version
    <root>/data/                      state/books/caches, shared across
                                      versions (never inside a release)

Update flow: check the latest GitHub release, download its tarball
next to the existing releases, byte-compile every .py and verify the
bundled VERSION matches the tag, atomically flip the `current`
symlink, prune old releases (current + previous are kept), restart.
The launcher rolls `current` back to `previous` if the app exits
before writing the health marker (data/healthy).

Versions are timestamp strings (vYYYY.MM.DD-HH.MM, see CLAUDE.md),
so "newer" is a plain string comparison.

When running from a plain checkout (development, or a git-clone
install) is_managed() is False: the update check still works, but
installing requires the managed layout.
"""

import json
import logging
import os
import py_compile
import shutil
import tarfile
import tempfile
import urllib.request

logger = logging.getLogger(__name__)

REPO = "danielvera64/tinto"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
HEADERS = {"User-Agent": "tinto-updater/1.0",
           "Accept": "application/vnd.github+json"}

# Directory holding the running code (a release dir in the managed
# layout; the checkout root otherwise)
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_PATH = os.path.join(APP_DIR, "VERSION")


def current_version() -> str:
    try:
        with open(VERSION_PATH) as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


def managed_root():
    """The managed-layout root, or None when running from a checkout."""
    parent = os.path.dirname(APP_DIR)
    root = os.path.dirname(parent)
    if (os.path.basename(parent) == "releases"
            and os.path.islink(os.path.join(root, "current"))):
        return root
    return None


def is_managed() -> bool:
    return managed_root() is not None


def check_latest(timeout=10):
    """Returns (tag, tarball_url) of the newest GitHub release, or
    (None, None) if the check fails (offline, rate limit, ...)."""
    try:
        req = urllib.request.Request(API_LATEST, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        return data.get("tag_name"), data.get("tarball_url")
    except Exception as exc:
        logger.warning("update check failed: %s", exc)
        return None, None


def is_newer(tag) -> bool:
    """True if tag is a newer version than the running one. Timestamp
    versions compare as plain strings; a 'dev' checkout treats any
    release as newer."""
    cur = current_version()
    return bool(tag) and tag != cur and (cur == "dev" or tag > cur)


def _set_link(link, target):
    """Atomically points `link` at `target` (rename over the old link)."""
    tmp = link + ".tmp"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link)


def _verify(dest, tag):
    """Sanity gates before a release may become `current`."""
    if not os.path.isfile(os.path.join(dest, "main.py")):
        raise RuntimeError("main.py missing from release")
    with open(os.path.join(dest, "VERSION")) as f:
        version = f.read().strip()
    if version != tag:
        raise RuntimeError(f"VERSION file says {version!r}, tag is {tag!r}")
    for root, dirs, files in os.walk(dest):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.endswith(".py"):
                py_compile.compile(os.path.join(root, name), doraise=True)


def _prune(releases_dir, keep):
    """Removes release dirs not in `keep` (paths, resolved)."""
    for entry in os.listdir(releases_dir):
        path = os.path.realpath(os.path.join(releases_dir, entry))
        if (os.path.isdir(path) and entry.startswith("v")
                and path not in keep):
            shutil.rmtree(path, ignore_errors=True)
            logger.info("pruned old release %s", entry)


def download_and_install(tag, tarball_url, status_cb=lambda msg: None):
    """Runs the full update: download -> unpack -> verify -> flip the
    `current` symlink -> prune. Raises on any failure, in which case
    the running installation is untouched. On success the new version
    runs after the next restart."""
    root = managed_root()
    if root is None:
        raise RuntimeError("not running from the managed layout")
    releases = os.path.join(root, "releases")
    dest = os.path.join(releases, tag)
    if os.path.exists(dest):
        shutil.rmtree(dest)  # leftover from an aborted attempt

    status_cb(f"Downloading {tag}…")
    req = urllib.request.Request(tarball_url, headers=HEADERS)
    fd, tmp_tar = tempfile.mkstemp(dir=releases, suffix=".tar.gz")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, \
                os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(resp, out)

        status_cb("Unpacking…")
        # GitHub tarballs wrap everything in a single top-level dir
        # (trusted source; extraction stays inside the temp dir)
        with tarfile.open(tmp_tar) as tar:
            top = tar.getnames()[0].split("/")[0]
            with tempfile.TemporaryDirectory(dir=releases) as td:
                tar.extractall(td)
                os.rename(os.path.join(td, top), dest)
    finally:
        if os.path.exists(tmp_tar):
            os.remove(tmp_tar)

    try:
        status_cb("Verifying…")
        _verify(dest, tag)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    status_cb("Installing…")
    current = os.path.join(root, "current")
    old_target = os.path.realpath(current)
    _set_link(os.path.join(root, "previous"), old_target)
    _set_link(current, dest)
    _prune(releases, keep={os.path.realpath(dest), old_target})
    logger.info("installed %s (previous: %s)", tag,
                os.path.basename(old_target))
