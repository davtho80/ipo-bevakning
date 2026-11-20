#!/usr/bin/env bash
# Daglig säkerhetskopia av databasen (dina markeringar finns bara där).
# Sparar de senaste 14 kopiorna i /var/lib/ipo-bevakning/backups.
set -euo pipefail
DB=/var/lib/ipo-bevakning/ipo.db
DIR=/var/lib/ipo-bevakning/backups
KEEP=14
mkdir -p "$DIR"
[ -f "$DB" ] || { echo "Ingen databas än"; exit 0; }
OUT="$DIR/ipo-$(date +%F).db"
sqlite3 "$DB" ".backup '$OUT'"
gzip -f "$OUT"
ls -1t "$DIR"/ipo-*.db.gz | tail -n +$((KEEP + 1)) | xargs -r rm --
echo "Säkerhetskopia: $OUT.gz"
