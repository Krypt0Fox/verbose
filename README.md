# Delta Key Bot v4.0

Discord bot that extracts Delta license keys automatically.

## Commands
| Command | Description |
|---------|-------------|
| `/key <url>` | Extract a key from a Platorelay link |
| `/history` | View your recently extracted keys (paginated) |
| `/stats` | Bot uptime, success rate & active extractions |
| `/leaderboard` | Top extractors across all users |
| `/ping` | Check bot latency |
| `/invite` | Get a link to add the bot to your server |
| `/help` | Show all commands |

## Features
- **Cross-device copy** — Copy Key modal works on iOS, Android & desktop
- **Raw Key button** — Sends key as a code block for easy copying
- **Paginated views** — History and leaderboard with page navigation
- **User-scoped interactions** — Only you can navigate your own pages
- **Auto-trimmed history** — Capped at 500 entries per user
- **Concurrent extraction** — Semaphore-limited parallel key extraction
- **Protected runtime** — Multi-layer obfuscation (no PyArmor needed)

## Setup
1. Set `DISCORD_TOKEN` environment variable
2. `pip install -r requirements.txt`
3. `python3 delta_bot.py`

## Hosting
Deployed on Railway as a worker process.

## v4.0 Changelog
- Fixed copy button not working on all devices
- Fixed buttons breaking on old messages (unique custom_ids)
- Fixed history numbering bug
- Replaced internal `_sem._value` with proper counter
- Added progress loop error handling
- Added page indicators and user-scoped pagination
- Capped per-user history at 500 entries
- Stronger obfuscation (5-layer: marshal → zlib → b64 → XOR → b64 + chunk scatter)
- Removed PyArmor runtime dependency
