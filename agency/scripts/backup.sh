#!/usr/bin/env bash
# نسخة احتياطية لكل العملاء: قاعدة كل عميل (pg_dump) + ملفات n8n + أسراره (مفتاح التشفير).
# شغّله بصلاحية root (ملفات n8n مملوكة للمستخدم 1000). يومياً من cron:
#   30 3 * * *  /opt/agency/scripts/backup.sh >> /var/log/agency-backup.log 2>&1
# ثم انسخ مجلد backups/ خارج السيرفر (Storage Box أو غيره): نسخة على نفس السيرفر ليست نسخة احتياطية.
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP_DAYS=${KEEP_DAYS:-14}
DEST="backups/$(date +%F)"
mkdir -p "$DEST"
chmod 700 backups "$DEST"

for env_file in clients/*.env; do
  [ -e "$env_file" ] || continue
  name=$(basename "$env_file" .env)
  docker compose exec -T postgres pg_dump -U postgres -Fc "$name" > "$DEST/$name.dump"
  tar -czf "$DEST/$name-files.tar.gz" "data/n8n/$name" "$env_file"
  echo "$(date '+%F %T') $name: $(du -h "$DEST/$name.dump" | cut -f1) db, $(du -h "$DEST/$name-files.tar.gz" | cut -f1) files"
done
cp .env "$DEST/agency.env"
chmod 600 "$DEST"/*
find backups -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +
