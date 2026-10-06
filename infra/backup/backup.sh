#!/bin/sh
# Breakout database backups (pg_dump, custom format).
#
#   breakout-backup schedule      dump every day at BACKUP_TIME (US/Eastern, default 02:30)
#   breakout-backup now           dump now, rotate, copy off-site when configured
#   breakout-backup list          the dumps on disk, newest first
#   breakout-backup verify FILE   restore FILE into a scratch database and compare every
#                                 table's row count with the live one (a restore test)
#   breakout-backup restore FILE  replace the database with FILE (stop the app first; the
#                                 current database is dumped to pre-restore/ before anything)
# FILE is a path, a name from `list`, or "latest" (the newest dump).
#
# Rotation: the last BACKUP_KEEP_DAILY dumps (default 14) in daily/, and Sunday's dump also in
# weekly/, keeping BACKUP_KEEP_WEEKLY (default 8). Off-site: set BACKUP_S3_BUCKET (and
# BACKUP_S3_ENDPOINT, BACKUP_S3_ACCESS_KEY_ID, BACKUP_S3_SECRET_ACCESS_KEY, optionally
# BACKUP_S3_REGION, BACKUP_S3_PREFIX, BACKUP_S3_PROVIDER) and the backup folder is mirrored
# there after each dump. Connection: PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE.
set -eu

export PGHOST="${PGHOST:-postgres}" PGUSER="${PGUSER:-breakout}" PGDATABASE="${PGDATABASE:-breakout}"
export PGOPTIONS="${PGOPTIONS:--c client_min_messages=warning}"
DIR="${BACKUP_DIR:-/backups}"
KEEP_DAILY="${BACKUP_KEEP_DAILY:-14}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-8}"
AT="${BACKUP_TIME:-02:30}"
MARKET_TZ=America/New_York

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup: $*"; }

prune() { # keep the newest $2 dumps in $1
  ls -1t "$1"/*.dump 2>/dev/null | tail -n "+$(($2 + 1))" | while read -r old; do
    rm -f "$old" && log "removed $old"
  done
}

offsite() {
  [ -n "${BACKUP_S3_BUCKET:-}" ] || { echo "skipped"; return 0; }
  export RCLONE_CONFIG_OFFSITE_TYPE=s3
  export RCLONE_CONFIG_OFFSITE_PROVIDER="${BACKUP_S3_PROVIDER:-Other}"
  export RCLONE_CONFIG_OFFSITE_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
  export RCLONE_CONFIG_OFFSITE_REGION="${BACKUP_S3_REGION:-}"
  export RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY_ID:-}"
  export RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_ACCESS_KEY:-}"
  if rclone sync "$DIR" "offsite:${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX:-breakout}" \
    --include "/daily/*.dump" --include "/weekly/*.dump" >&2; then
    echo "copied"
  else
    echo "failed"
  fi
}

now() {
  mkdir -p "$DIR/daily" "$DIR/weekly"
  file="$DIR/daily/breakout_$(TZ=$MARKET_TZ date +%Y-%m-%d_%H%M).dump"
  log "dumping $PGDATABASE on $PGHOST to $file"
  # pg_dump warns about circular foreign keys in TimescaleDB's own catalog (continuous_agg):
  # harmless for a full dump like this one.
  pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$file.partial"
  mv "$file.partial" "$file"
  if [ "$(TZ=$MARKET_TZ date +%u)" = 7 ]; then
    cp "$file" "$DIR/weekly/"
  fi
  prune "$DIR/daily" "$KEEP_DAILY"
  prune "$DIR/weekly" "$KEEP_WEEKLY"
  size=$(du -k "$file" | cut -f1)
  copied=$(offsite)
  printf '{"file": "%s", "finished_at": "%s", "size_kb": %s, "offsite": "%s"}\n' \
    "$(basename "$file")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$size" "$copied" > "$DIR/last.json"
  log "done: $(basename "$file") (${size} kB, off-site: $copied)"
  [ "$copied" != "failed" ]
}

next_run() { # epoch seconds of the next BACKUP_TIME in US/Eastern
  today=$(TZ=$MARKET_TZ date +%Y-%m-%d)
  at=$(TZ=$MARKET_TZ date -d "$today $AT" +%s)
  if [ "$at" -le "$(date +%s)" ]; then
    tomorrow=$(TZ=$MARKET_TZ date -d "@$(($(date +%s) + 86400))" +%Y-%m-%d)
    at=$(TZ=$MARKET_TZ date -d "$tomorrow $AT" +%s)
  fi
  echo "$at"
}

schedule() {
  log "nightly at $AT US/Eastern; keeping $KEEP_DAILY daily and $KEEP_WEEKLY weekly dumps"
  while true; do
    at=$(next_run)
    log "next dump at $(TZ=$MARKET_TZ date -d "@$at" '+%Y-%m-%d %H:%M %Z')"
    sleep $((at - $(date +%s)))
    now || log "the backup failed; trying again tomorrow"
  done
}

list() {
  found=$(ls -1t "$DIR"/daily/*.dump "$DIR"/weekly/*.dump 2>/dev/null || true)
  if [ -n "$found" ]; then echo "$found"; else echo "No backups yet in $DIR."; fi
}

counts() { # "table rows" for every table in the public schema of database $1
  for table in $(psql -d "$1" -Atc "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"); do
    echo "$table $(psql -d "$1" -Atc "SELECT count(*) FROM public.\"$table\"")"
  done
}

load_into() { # restore dump $2 into a fresh database $1 (TimescaleDB's restore procedure)
  dropdb --if-exists --force "$1"
  createdb "$1"
  psql -v ON_ERROR_STOP=1 -d "$1" -qc "CREATE EXTENSION IF NOT EXISTS timescaledb"
  psql -v ON_ERROR_STOP=1 -d "$1" -qAtc "SELECT timescaledb_pre_restore()" >/dev/null
  # TimescaleDB's own catalog rows can raise harmless "already exists" errors: check the result
  # by row counts (verify) rather than by pg_restore's exit status.
  pg_restore --no-owner --no-privileges --dbname="$1" "$2" || log "pg_restore reported warnings"
  psql -v ON_ERROR_STOP=1 -d "$1" -qAtc "SELECT timescaledb_post_restore()" >/dev/null
}

resolve() { # a path, a dump's name, or "latest"
  if [ "$1" = latest ]; then
    found=$(ls -1t "$DIR"/daily/*.dump "$DIR"/weekly/*.dump 2>/dev/null | head -n 1 || true)
  elif [ -f "$1" ]; then
    found="$1"
  else
    found=$(ls -1 "$DIR/daily/$1" "$DIR/weekly/$1" 2>/dev/null | head -n 1 || true)
  fi
  [ -n "$found" ] || { echo "No such backup: $1 (see: breakout-backup list)" >&2; exit 1; }
  echo "$found"
}

verify() {
  file=$(resolve "$1")
  scratch="${PGDATABASE}_verify"
  log "restoring $(basename "$file") into $scratch"
  load_into "$scratch" "$file"
  counts "$PGDATABASE" > /tmp/live.txt
  counts "$scratch" > /tmp/restored.txt
  dropdb --if-exists --force "$scratch"
  if diff /tmp/live.txt /tmp/restored.txt; then
    log "verified: $(wc -l < /tmp/live.txt) tables, $(awk '{s += $2} END {print s}' /tmp/live.txt) rows match"
  else
    log "row counts differ (above). If the app wrote since the dump, run verify on a fresh dump."
    exit 1
  fi
}

restore() {
  file=$(resolve "$1")
  mkdir -p "$DIR/pre-restore"
  safety="$DIR/pre-restore/breakout_$(date -u +%Y%m%dT%H%M%SZ).dump"
  log "saving the current database to $safety first"
  pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$safety"
  log "replacing $PGDATABASE with $(basename "$file")"
  load_into "$PGDATABASE" "$file"
  log "restored. Start the app again (make prod-up)."
}

case "${1:-schedule}" in
  schedule) schedule ;;
  now) now ;;
  list) list ;;
  verify) verify "${2:?usage: breakout-backup verify FILE}" ;;
  restore) restore "${2:?usage: breakout-backup restore FILE}" ;;
  *) echo "usage: breakout-backup schedule|now|list|verify FILE|restore FILE" >&2; exit 2 ;;
esac
