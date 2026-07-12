# Telegram Button-Poster Bot

DM this bot a message (text, or a photo with a caption). It parses out any
`[Label - URL]` tags into real inline buttons, shows you a preview with
Confirm/Cancel, and — once you confirm — posts it to your channel or group.

## 1. Create the bot
1. Message **@BotFather** on Telegram → `/newbot` → follow the prompts.
2. Copy the token it gives you (looks like `123456789:AA...`). This is `BOT_TOKEN`.

## 2. Add it to your channel/group
1. Add the bot to your channel/group.
2. Promote it to **admin** with "Post messages" permission (and "Manage
   messages" if you want it to edit/delete later).

## 3. Get the IDs you need
- **CHANNEL_ID**: if your channel has a public username, you can use
  `@yourchannelusername` directly. For private channels/groups, forward any
  message from that channel to **@JsonDumpBot** (or **@userinfobot**) and
  read the numeric `chat.id` (it'll look like `-1001234567890`).
- **ADMIN_IDS**: message **@userinfobot** yourself to get your numeric
  Telegram user ID. This restricts who's allowed to DM the bot and trigger a
  post — set it to your own ID (comma-separate for multiple people).

## 4. Message format
Send the bot any text or photo caption. Anywhere you write:

```
[REGISTER NOW - https://vireonwebsite.com.ng]
[JOIN CHANNEL - https://t.me/yourchannel]
```

each `[Label - URL]` becomes its own button (stacked one per row), and the
tags themselves are stripped out of the visible text. Example message to
send the bot:

```
🚀 Vireon Premiere is open!

Surveys, remote work, CallCash, and more — all unlocked with one
registration.

[REGISTER NOW - https://vireonwebsite.com.ng]
[IS IT LEGIT? - https://vireonwebsite.com.ng/is-vireon-legit]
```

The bot replies with a preview (same buttons + a Confirm/Cancel row). Tap
**✅ Post to channel** and it goes live; **❌ Cancel** discards it.

## 5. Download forwarded audio

Forward the bot one or more audio files (or voice notes). It converts each
one to MP3 and sends it right back to you in the chat — tap any file to
download it straight from Telegram. If you forward several at once, it
waits a beat for them all to land, then sends them back numbered in the
order they were forwarded (`01 - Track One.mp3`, `02 - Track Two.mp3`,
...) so the sequence is preserved. A single forward is sent back with its
original name (no number).

Conversion uses a bundled `ffmpeg` binary (via the `imageio-ffmpeg` pip
package), so no separate system install is needed.

- **AUDIO_BATCH_DELAY** (optional): seconds to wait after the last forward
  before converting and sending the batch. Defaults to `1.5`.

The `/start` menu has separate sections for text/photo posts and audio
downloads — tap a button to see instructions for that feature.

## 6. Deploy to Railway
1. Push this folder to a new GitHub repo.
2. On Railway: **New Project → Deploy from GitHub repo** → pick the repo.
3. Railway auto-detects Python and the `Procfile` (runs `python bot.py` as a
   worker — no public URL needed since this uses polling).
4. Under **Variables**, add:
   - `BOT_TOKEN`
   - `CHANNEL_ID`
   - `ADMIN_IDS`
5. Deploy. Check the logs for `Bot starting (polling)...` to confirm it's live.

## 7. Test locally (optional)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values, then export them or use a tool like `honcho`
python bot.py
```

## Notes
- `ADMIN_IDS` matters — without it, anyone who finds your bot could post to
  your channel. Always set it in production.
- Photo captions are capped at 1024 characters by Telegram; long promo copy
  should go out as text-only posts instead.
- The bot uses polling, which is the simplest option for a Railway worker.
  If you outgrow this (e.g. want it to also react to comments), it can be
  switched to webhooks later.
