#!/bin/sh
# Restore test for the backup image (CI and `make backup-test`). Given a migrated, scratch
# database (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE; reachable from the host network), it
#   1. seeds tickers and two years of daily bars, compressing the chunks older than 180 days;
#   2. checks rotation (old dumps pruned to BACKUP_KEEP_DAILY);
#   3. dumps the database and restores the dump into a scratch database (`verify`: every table's
#      row count must match);
#   4. damages the database (drops a table, deletes rows) and restores the dump in place, then
#      checks the rows, the hypertables and their compressed chunks are back.
# Usage: infra/backup/test-restore.sh IMAGE
set -eu

IMAGE="${1:?usage: test-restore.sh IMAGE}"
OUT=$(mktemp -d)

container() {
  docker run --rm --network host -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE \
    -e BACKUP_KEEP_DAILY -v "$OUT:/backups" "$@"
}
backup() { container "$IMAGE" "$@"; }
sql() { container --entrypoint psql "$IMAGE" -v ON_ERROR_STOP=1 -Atqc "$1"; }
cleanup() { container --entrypoint sh "$IMAGE" -c 'rm -rf /backups/*' >/dev/null 2>&1 || true; rm -rf "$OUT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

state() { # what must survive a restore
  sql "SELECT (SELECT count(*) FROM tickers) || ' tickers, '
    || (SELECT count(*) FROM daily_bars) || ' bars, '
    || (SELECT count(*) FROM corporate_actions) || ' actions, '
    || (SELECT count(*) FROM timescaledb_information.hypertables) || ' hypertables, '
    || (SELECT count(*) FROM timescaledb_information.chunks WHERE is_compressed) || ' compressed chunks, '
    || 'revision ' || (SELECT version_num FROM alembic_version)"
}

echo "== seed"
sql "INSERT INTO tickers (symbol, name, exchange, type, first_seen, last_seen)
     SELECT 'TST' || i, 'Test ' || i, 'NASDAQ', 'common', current_date - 730, current_date
     FROM generate_series(1, 5) i"
sql "INSERT INTO daily_bars (ticker_id, date, open, high, low, close, volume, source)
     SELECT t.id, d::date, 10, 11, 9, 10.5, 1000 * t.id, 'test'
     FROM tickers t CROSS JOIN generate_series(current_date - 730, current_date - 1, '1 day') d"
sql "INSERT INTO corporate_actions (ticker_id, ex_date, kind, value, source)
     SELECT id, current_date - 400, 'split', 2.0, 'test' FROM tickers"
compressed=$(sql "SELECT count(compress_chunk(c, if_not_compressed => true))
                  FROM show_chunks('daily_bars', older_than => INTERVAL '180 days') c")
[ "$compressed" -ge 1 ] || fail "no chunk was compressed"
before=$(state)
echo "$before"

echo "== rotation"
container --entrypoint sh "$IMAGE" -c '
  mkdir -p /backups/daily
  for day in 01 02 03; do
    touch -d "2020-01-$day 02:30" "/backups/daily/breakout_2020-01-${day}_0230.dump"
  done'
BACKUP_KEEP_DAILY=2 backup now
kept=$(ls "$OUT/daily")
[ "$(echo "$kept" | wc -l)" -eq 2 ] || fail "expected 2 dumps after rotation, got: $kept"
echo "$kept" | grep -q "breakout_2020-01-03_0230.dump" || fail "kept the wrong dumps: $kept"
dump=$(ls -1t "$OUT/daily" | head -n 1)
grep -q "\"file\": \"$dump\"" "$OUT/last.json" || fail "last.json doesn't name $dump"
grep -q '"offsite": "skipped"' "$OUT/last.json" || fail "last.json: $(cat "$OUT/last.json")"
backup list

echo "== verify into a scratch database"
backup verify latest

echo "== damage, then restore in place"
sql "DROP TABLE corporate_actions"
sql "DELETE FROM daily_bars WHERE ticker_id = (SELECT min(id) FROM tickers)"
backup restore "$dump"
after=$(state)
echo "$after"
[ "$after" = "$before" ] || fail "restored state differs: was '$before'"
[ -n "$(ls "$OUT/pre-restore")" ] || fail "no safety dump in pre-restore/"
echo "== restore test passed"
