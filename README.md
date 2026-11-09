# Telegram Auto Translation Bot (No Database)

A production-ready, asynchronous Telegram bot that automatically detects any language and translates messages into English. Includes mandatory channel/group join (force-join), full admin panel, user management, ban system, and broadcasting.

**All state is kept in memory** — no database required. Data (users, bans, counters) resets when the bot restarts.

## Features

- **Auto Translation**: Users send text in any language → receive English translation. No commands needed.
- **Force Join**: Users must join a required channel **and** group before using the bot. Membership is verified via Telegram API (`getChatMember`).
- **Admin Panel** (`/admin`): Statistics, user list, ban/unban, search, broadcast, force-join settings.
- **Broadcast**: Send text, photos, videos or documents to all known non-banned users with confirmation and progress.
- **User Management**: In-memory registration, activity tracking, translation counters, ban status.
- **Statistics**: Runtime counters (translations, failures, broadcasts, uptime).
- **Heroku Ready**: Worker dyno + environment variables.
- **Reliable**: Fully async, rate-limit handling, graceful error recovery, no blocking of the event loop.

## Project Structure

```
telegram_auto_translate_bot/
├── main.py              # Bot entry point & all handlers
├── config.py            # Environment configuration
├── requirements.txt
├── Procfile             # Heroku worker process
├── runtime.txt
├── .env.example
└── README.md
```

## Required Environment Variables

| Variable            | Description                                      | Example                          |
|---------------------|--------------------------------------------------|----------------------------------|
| `BOT_TOKEN`         | Bot token from @BotFather                        | `123456:ABC-DEF...`              |
| `ADMIN_IDS`         | Comma-separated Telegram user IDs of admins      | `123456789,987654321`            |
| `FORCE_CHANNEL_ID`  | Channel ID (must start with `-100`)              | `-1001234567890`                 |
| `FORCE_GROUP_ID`    | Group/supergroup ID (must start with `-100`)     | `-1009876543210`                 |
| `CHANNEL_LINK`      | Public invite link to the channel                | `https://t.me/mychannel`         |
| `GROUP_LINK`        | Public/private invite link to the group          | `https://t.me/+AbCdEf...`        |
| `LOG_LEVEL`         | Logging level (optional)                         | `INFO`                           |

## How to Obtain IDs & Links

### 1. Bot Token
1. Open Telegram → **@BotFather**
2. Send `/newbot` and follow the instructions.
3. Copy the token.

### 2. Your Admin Telegram ID
1. Search for **@userinfobot** or **@getidsbot**.
2. Start the bot — it replies with your numeric ID.
3. Put that number in `ADMIN_IDS`.

### 3. Channel & Group IDs
1. Create a **Channel** and a **Group** (or Supergroup).
2. Add your bot to both as **Administrator** (required for `getChatMember`).
3. Forward a message from the channel/group to **@userinfobot** / **@getidsbot** or use **@RawDataBot**.
4. The ID looks like `-100xxxxxxxxxx`. Use the full number including the `-100` prefix.
5. Create invite links from Channel/Group Info.

### Important
- The bot **must** be an administrator in both the channel and the group.
- Without this, membership checks always fail and users cannot pass force-join.

## Local Development

```bash
cd telegram_auto_translate_bot

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your values

python main.py
```

## Deploy on Heroku

```bash
heroku login
heroku create your-bot-name

heroku config:set BOT_TOKEN="your_token"
heroku config:set ADMIN_IDS="123456789"
heroku config:set FORCE_CHANNEL_ID="-1001234567890"
heroku config:set FORCE_GROUP_ID="-1009876543210"
heroku config:set CHANNEL_LINK="https://t.me/yourchannel"
heroku config:set GROUP_LINK="https://t.me/yourgroup"

git init
git add .
git commit -m "Initial deploy"
heroku git:remote -a your-bot-name
git push heroku main

heroku ps:scale worker=1
heroku logs --tail
```

> Use a **worker** dyno (already defined in `Procfile`).

## Important Notes About In-Memory Storage

- Users, bans, translation counters and statistics are stored **only in RAM**.
- When the bot restarts (Heroku dyno restart, crash, deploy, etc.) all of this data is lost.
- Broadcast can only reach users who have interacted with the bot **since the current process started**.
- Force-join and translation still work perfectly after restart.

## Testing Checklist

1. `/start` without joining → force-join keyboard appears.
2. Join only one destination → verify still fails and shows what’s missing.
3. Join both → verify succeeds → welcome + translation works.
4. Send text in Hindi / Arabic / Russian / Chinese / Spanish → correct English translation.
5. Send very long text → bot splits and translates correctly.
6. `/admin` as non-admin → denied.
7. `/admin` as admin → panel opens, statistics show runtime numbers.
8. Ban a user → they receive “banned” message and cannot translate.
9. Unban → access restored.
10. Broadcast text → confirmation → progress → summary.
11. Leave the channel/group → next message triggers force-join again.
12. Admin can always use the bot even without joining.

## Commands

| Command     | Who      | Description                    |
|-------------|----------|--------------------------------|
| `/start`    | Everyone | Welcome + force-join check     |
| `/admin`    | Admins   | Open admin panel               |
| `/ban ID`   | Admins   | Ban a user                     |
| `/unban ID` | Admins   | Unban a user                   |
| `/user ID`  | Admins   | Show user details              |
| `/cancel`   | Admins   | Cancel current admin action    |

## Translation

- Uses `deep-translator` (Google Translate backend) — free, no API key.
- Source language is auto-detected.
- Long messages are intelligently chunked.
- Translation runs in a thread-pool executor so the asyncio event loop is never blocked.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Force-join always fails | Ensure bot is **admin** in both channel and group. Double-check the `-100...` IDs. |
| `getChatMember` errors | Bot must be able to see members (admin rights). |
| Bot not responding | Check `heroku logs --tail`. Ensure worker dyno is up: `heroku ps`. |
| Translation fails | Google Translate may temporarily rate-limit. Retry later. |
| FloodWait / RetryAfter | Already handled with backoff. |

---

Lightweight, fast, no database dependency. Ready for Heroku.
