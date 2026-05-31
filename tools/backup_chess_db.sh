#!/usr/bin/env bash
set -euo pipefail
SRC=/opt/chess-arena/data/chess_arena.db
DEST_DIR=/mnt/cosmem/gulu1-1415708756/chess-arena/backups
LATEST=/mnt/cosmem/gulu1-1415708756/chess-arena/chess_arena.latest.db
TS=$(date +%Y%m%d%H%M%S)
mkdir -p "$DEST_DIR"
TMP="/tmp/chess_arena_backup_$TS.db"
# Use SQLite online backup API so backup is consistent even while services run.
python3 - <<PY
import sqlite3
src = sqlite3.connect('$SRC')
dst = sqlite3.connect('$TMP')
src.backup(dst)
dst.close(); src.close()
PY
cp "$TMP" "$DEST_DIR/chess_arena.$TS.db"
cp "$TMP" "$LATEST"
rm -f "$TMP"
# keep last 288 timestamped backups (~24h if every 5min); latest pointer is kept separately
find "$DEST_DIR" -maxdepth 1 -name 'chess_arena.[0-9]*.db' -type f | sort | head -n -288 | xargs -r rm -f
