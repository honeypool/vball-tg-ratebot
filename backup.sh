#!/bin/sh
# Daily SQLite backup, keeps the last 14. Install with crontab -e:
#   30 3 * * * /opt/vball-ratebot/backup.sh >> /opt/vball-ratebot/data/backup.log 2>&1
set -e
cd "$(dirname "$0")"
mkdir -p data/backups
NAME="backup-$(date +%F).sqlite"
docker compose exec -T bot python -c "
import sqlite3
src = sqlite3.connect('/app/data/bot.sqlite')
dst = sqlite3.connect('/app/data/backups/$NAME')
src.backup(dst)
dst.close()
"
ls -1t data/backups/backup-*.sqlite | tail -n +15 | xargs -r rm --
echo "$(date -Is) saved data/backups/$NAME"
