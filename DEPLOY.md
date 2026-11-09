# Running and deploying

## 1. Telegram setup (do this for the test bot + test group, and again for prod)

1. **BotFather**
   - `/setprivacy` → *Enable* (the bot must not read group messages).
   - `/setdescription`, `/setabouttext`, `/setcommands` → `start - Open the menu`.
2. **Group**
   - The group must be a *supergroup*.
   - Add the bot as an **administrator** with only **Manage tags** switched on. Turn every other right off.
   - Find the group id, which is negative, like `-100…`. One way: temporarily add `@RawDataBot` to the group, read `chat.id`, then remove it.
3. **Your user id** (for `ADMIN_ID`): message `@userinfobot`.

Group admins and the owner may not accept member tags, which is a Telegram limitation. The bot logs a warning and skips them.

## 2. Local run with the test bot (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt pytest
copy .env.example .env      # then put the TEST token / group / your id in .env
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python spike.py <your_user_id>   # once: checks tags + rich messages
.\.venv\Scripts\python bot.py
```

Manual nightly run: `.\.venv\Scripts\python nightly.py --once`.
Stop `bot.py` first or run it in a second terminal. It only needs the DB and the token.

## 3. Production on the droplet (first time)

```sh
# as root on the droplet
curl -fsSL https://get.docker.com | sh
git clone <repo-url> /opt/vball-ratebot
cd /opt/vball-ratebot
cp .env.example .env && nano .env     # PRODUCTION token, prod GROUP_ID, ADMIN_ID
chmod +x deploy.sh backup.sh
./deploy.sh
crontab -e    # add:  30 3 * * * /opt/vball-ratebot/backup.sh >> /opt/vball-ratebot/data/backup.log 2>&1
```

Never run the same token in two places at once. Telegram allows only one polling client per token.

## 4. Updating production

```sh
cd /opt/vball-ratebot && ./deploy.sh
```

## Useful commands

| What | Command |
|---|---|
| Live logs | `docker compose logs -f bot` |
| Restart | `docker compose restart bot` |
| Recalculate now | `docker compose exec bot python nightly.py --once` |
| Data export | send `/export` to the bot as the admin |
| Restore backup | `docker compose stop bot && cp data/backups/backup-YYYY-MM-DD.sqlite data/bot.sqlite && rm -f data/bot.sqlite-wal data/bot.sqlite-shm && chown 1000:1000 data/bot.sqlite && docker compose start bot` |
