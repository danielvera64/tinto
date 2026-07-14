#!/bin/sh
# Tinto launcher for the managed (auto-update) layout:
#
#   <root>/run.sh                    this script (copy it OUTSIDE the
#                                    releases so updates can't break it)
#   <root>/current -> releases/<v>   the version that runs
#   <root>/previous -> releases/<v>  rollback target (set by updates)
#   <root>/data/                     state, books, caches
#
# The app writes data/healthy (containing its version) once it is up.
# If it exits without doing so, this script rolls `current` back to
# `previous`, so a broken update self-heals on the next restart.
# Run it from systemd with Restart=always.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
CUR="$ROOT/current"
PREV="$ROOT/previous"
DATA="$ROOT/data"
MARKER="$DATA/healthy"

mkdir -p "$DATA/books"
VERSION="$(cat "$CUR/VERSION" 2>/dev/null || echo unknown)"
rm -f "$MARKER"

python3 "$CUR/main.py" \
    --books-dir "$DATA/books" \
    --state-file "$DATA/reader_state.json" \
    "$@"
CODE=$?

HEALTHY="$(cat "$MARKER" 2>/dev/null || echo none)"
if [ "$HEALTHY" != "$VERSION" ] && [ -L "$PREV" ]; then
    PREV_T="$(readlink "$PREV")"
    CUR_T="$(readlink "$CUR")"
    if [ "$PREV_T" != "$CUR_T" ]; then
        echo "tinto: $VERSION exited before becoming healthy;" \
             "rolling back to $(basename "$PREV_T")" >&2
        ln -sfn "$PREV_T" "$CUR"
    fi
fi
exit $CODE
