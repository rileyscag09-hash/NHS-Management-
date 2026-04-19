# NHS Discord Bot

Small Discord bot for your NHS server with:

- Welcome embeds in channel `1485780276239143006`
- Kick and ban slash commands with DM embeds
- Verification embed with a Melonly button in channel `1486873233377460437`
- ERLC admin slash commands for kick, ban, and announcements

## Setup

1. Install Python 3.11+ if needed.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env`
4. Add your bot token, server ID, and ERLC API key to `.env`
5. Start the bot:

```powershell
python bot.py
```

## Deploy To Render

1. Push this folder to GitHub.
2. In Render, create a new `Web Service`.
3. Connect your GitHub repo.
4. Use these settings:

```text
Build Command: pip install -r requirements.txt
Start Command: python bot.py
```

5. Add these environment variables in Render:

```text
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_server_id_here
ERLC_API_KEY=your_erlc_api_key_here
ADMIN_ROLE_NAME=Admin
PYTHON_VERSION=3.11.9
```

6. Deploy the service.

Render expects web services to bind to a port, so this bot starts a small health-check server automatically using Render's `PORT` variable while the Discord bot runs in the same process.

## Required Bot Permissions

- View Channels
- Send Messages
- Embed Links
- Use Slash Commands
- Kick Members
- Ban Members
- Read Message History

Also enable these privileged intents in the Discord Developer Portal:

- `SERVER MEMBERS INTENT`

## Commands

- `/kick`
- `/ban`
- `/verification`
- `/erlc_kick`
- `/erlc_ban`
- `/erlc_announce`

## Notes

- The bot posts the verification embed automatically on startup if it does not already find one in the verification channel.
- If a user's DMs are closed, the kick or ban will still go through.
- The ERLC slash commands require the Discord role named by `ADMIN_ROLE_NAME` and a valid `ERLC_API_KEY`.
