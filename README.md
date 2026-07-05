# Delta Key Bot

Discord bot that extracts Delta license keys automatically.

## Commands
| Command | Description |
|---------|-------------|
| `/key <url>` | Extract a key from a Platorelay link |
| `/history` | View your recently extracted keys |
| `/stats` | Bot uptime & success rate |
| `/ping` | Check bot latency |
| `/invite` | Get a link to add the bot to your server |
| `/help` | Show all commands |

## Setup
1. Set `DISCORD_TOKEN` environment variable
2. `pip install -r requirements.txt`
3. `python3 delta_bot.py`

## Hosting
Deployed on Railway as a worker process.
