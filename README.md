# Delta Key Bot — Setup Guide

## Step 1 — Create your Discord Bot

1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it (e.g. "Delta Key Bot")
3. Go to **Bot** tab → click **Add Bot**
4. Under **Token** → click **Reset Token** → copy it (you'll need it)
5. Scroll down → enable **Message Content Intent**
6. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot` + `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Read Message History`
7. Copy the generated URL → open it → invite bot to your server

## Step 2 — Host on Railway (free)

1. Go to https://railway.app → sign up with GitHub
2. Click **New Project → Deploy from GitHub repo**
3. Push these files to a GitHub repo first:
   - `delta_bot.py`
   - `requirements.txt`
4. In Railway, go to your project → **Variables** tab
5. Add variable: `DISCORD_TOKEN` = (your bot token from Step 1)
6. Railway auto-installs from requirements.txt and runs the bot 24/7

## Step 3 — Run locally (optional / for testing)

```bash
pip install -r requirements.txt
export DISCORD_TOKEN="your-token-here"
python3 delta_bot.py
```

## Commands

| Command | Description |
|---|---|
| `/key <url>` | Extract a Delta key from a Platorelay link |
| `/history` | Show all keys extracted this session |
| `/stats` | Uptime, success rate, total keys |
| `/ping` | Check bot latency |
| `/help` | Show all commands |

Prefix versions also work: `!key <url>`, `!history`, etc.
