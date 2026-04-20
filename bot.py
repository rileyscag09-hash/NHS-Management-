import logging
import os
import threading
import asyncio
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

WELCOME_CHANNEL_ID = 1485780276239143006
VERIFICATION_CHANNEL_ID = 1486873233377460437
SUPPORT_CHANNEL_ID = 1485794545261219922
VERIFY_URL = "https://melonly.xyz/verify/7451655967730569216/7451709577638187008"

EMBED_COLOR = discord.Color.from_rgb(22, 163, 74)
ALERT_COLOR = discord.Color.red()

REACTION_ROLES_LABEL = "🎭・reaction-roles"
RULES_LABEL = "📖・rules-and-regulations"
SERVER_INFO_LABEL = "ℹ️・server-information"
APPLY_LABEL = "📩・apply-here"
HELP_DESK_LABEL = "🆘・help-desk"
TICKETS_LABEL = "🎫・tickets"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("nhs-bot")


def is_cloudflare_rate_limit_error(exc: discord.HTTPException) -> bool:
    response_text = str(exc)
    return exc.status == 429 and ("Error 1015" in response_text or "Cloudflare" in response_text)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"NHS bot is running.")

    def log_message(self, format: str, *args) -> None:
        return


def start_healthcheck_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("Health check server listening on port %s", port)
    server.serve_forever()


class VerificationView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Verify Now",
                url=VERIFY_URL,
                style=discord.ButtonStyle.link,
            )
        )


class NHSBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )
        self.message_queue: asyncio.Queue[tuple[discord.abc.Messageable, dict]] = asyncio.Queue()
        self.message_worker: asyncio.Task | None = None
        self.verification_message_checked = False

    async def setup_hook(self) -> None:
        self.add_view(VerificationView())
        self.message_worker = asyncio.create_task(self.process_message_queue())

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s guild commands.", len(synced))
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global commands.", len(synced))

    async def process_message_queue(self) -> None:
        while True:
            channel, kwargs = await self.message_queue.get()
            try:
                await self.send_with_backoff(channel, **kwargs)
                await asyncio.sleep(1.25)
            finally:
                self.message_queue.task_done()

    async def send_with_backoff(self, channel: discord.abc.Messageable, **kwargs) -> discord.Message | None:
        delay = 2.0
        for attempt in range(4):
            try:
                return await channel.send(**kwargs)
            except discord.HTTPException as exc:
                is_retryable = exc.status == 429 or 500 <= exc.status < 600
                if not is_retryable or attempt == 3:
                    logger.warning("Failed to send message after %s attempt(s): %s", attempt + 1, exc)
                    return None

                retry_after = getattr(exc, "retry_after", None)
                wait_time = retry_after if retry_after is not None else delay
                logger.warning(
                    "Discord rate limit/server error while sending message. Retrying in %.2fs.",
                    wait_time,
                )
                await asyncio.sleep(wait_time)
                delay *= 2

        return None

    async def queue_message(self, channel: discord.abc.Messageable, **kwargs) -> None:
        await self.message_queue.put((channel, kwargs))


bot: NHSBot | None = None


def verification_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛡️ Community Verification & Whitelist",
        description=(
            "Welcome to the National Health Service! To ensure a high-quality roleplay "
            "experience and maintain server security, we utilize Melonly for verification "
            "and NHS for our automated whitelisting system."
        ),
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Verification Steps",
        value=(
            "1. Click the **Verify Now** button below.\n"
            "2. Log in to the Melonly dashboard.\n"
            "3. Link your Roblox and Discord accounts.\n"
            "4. Ensure your Discord username matches your Melonly profile."
        ),
        inline=False,
    )
    embed.add_field(
        name="NHS Whitelisting",
        value=(
            "Once you have verified via Melonly, NHS will automatically sync your roles "
            "and grant you access to the server."
        ),
        inline=False,
    )
    embed.add_field(
        name="Support",
        value=(
            "Having issues with National Health Service or Melonly? Head over to "
            f"<#{SUPPORT_CHANNEL_ID}> and our staff team will assist you shortly."
        ),
        inline=False,
    )
    embed.set_footer(text="National Health Service")
    return embed


def welcome_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="🏨 Welcome to National Health Service!",
        description=(
            f"Hello, {member.mention}, you've checked into reception please take a seat in the waiting area!\n\n"
            f"Choose your department in: {REACTION_ROLES_LABEL}"
        ),
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Get Started",
        value=(
            f"• Verify in <#{VERIFICATION_CHANNEL_ID}>\n"
            f"• Read {RULES_LABEL}\n"
            f"• Check {SERVER_INFO_LABEL}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Want to Join Us?",
        value=f"Apply in {APPLY_LABEL}",
        inline=False,
    )
    embed.add_field(
        name="Need Help?",
        value=f"Visit {HELP_DESK_LABEL} or {TICKETS_LABEL}",
        inline=False,
    )
    embed.set_footer(text="Enjoy your stay and stay professional. | NHS Management")
    return embed


def build_action_embed(title: str, description: str, reason: str) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=ALERT_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    return embed


def get_guild_bot_member(guild: discord.Guild) -> discord.Member | None:
    if bot is None or bot.user is None:
        return guild.me
    return guild.me or guild.get_member(bot.user.id)


def moderation_dm_embed(
    action: str,
    guild_name: str,
    moderator: discord.abc.User,
    reason: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"You have been {action}",
        description=f"You have been **{action}** from **{guild_name}**.",
        color=ALERT_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(
        name="Moderator",
        value=f"{moderator} (`{moderator.id}`)",
        inline=False,
    )
    embed.add_field(
        name="Time",
        value=discord.utils.format_dt(datetime.now(timezone.utc), style="F"),
        inline=False,
    )
    embed.set_footer(text="National Health Service Moderation")
    return embed


async def ensure_verification_message() -> None:
    if bot.verification_message_checked:
        return

    channel = bot.get_channel(VERIFICATION_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(VERIFICATION_CHANNEL_ID)
        except discord.DiscordException as exc:
            logger.warning("Could not fetch verification channel: %s", exc)
            return

    if not isinstance(channel, discord.TextChannel):
        logger.warning("Verification channel is not a text channel.")
        return

    try:
        async for message in channel.history(limit=25):
            if (
                message.author == bot.user
                and message.embeds
                and message.embeds[0].title == "🛡️ Community Verification & Whitelist"
            ):
                bot.verification_message_checked = True
                return
    except discord.DiscordException as exc:
        logger.warning("Could not inspect verification channel history: %s", exc)

    await bot.queue_message(channel, embed=verification_embed(), view=VerificationView())
    bot.verification_message_checked = True
    logger.info("Posted verification message in #%s.", channel.name)


async def on_ready() -> None:
    if bot is None or bot.user is None:
        return
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)
    await ensure_verification_message()


async def on_member_join(member: discord.Member) -> None:
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(WELCOME_CHANNEL_ID)
        except discord.DiscordException as exc:
            logger.warning("Could not fetch welcome channel: %s", exc)
            return

    if not isinstance(channel, discord.TextChannel):
        logger.warning("Welcome channel is not a text channel.")
        return

    await bot.queue_message(channel, embed=welcome_embed(member))


@app_commands.default_permissions(manage_guild=True)
async def verification(interaction: discord.Interaction) -> None:
    if interaction.channel is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    await bot.queue_message(interaction.channel, embed=verification_embed(), view=VerificationView())
    await interaction.response.send_message(
        "Verification message sent.",
        ephemeral=True,
    )


@app_commands.describe(member="Member to kick", reason="Reason for the kick")
@app_commands.default_permissions(kick_members=True)
async def kick_member(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided.",
) -> None:
    if interaction.guild is None or interaction.user is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    if member == interaction.user:
        await interaction.response.send_message(
            "You cannot kick yourself.",
            ephemeral=True,
        )
        return

    moderator = interaction.user if isinstance(interaction.user, discord.Member) else None
    bot_member = get_guild_bot_member(interaction.guild)

    if moderator and member.top_role >= moderator.top_role and interaction.guild.owner_id != moderator.id:
        await interaction.response.send_message(
            "You cannot kick that member because their role is higher than or equal to yours.",
            ephemeral=True,
        )
        return

    if bot_member is None or member.top_role >= bot_member.top_role:
        await interaction.response.send_message(
            "I cannot kick that member because their role is higher than or equal to mine.",
            ephemeral=True,
        )
        return

    try:
        await member.send(
            embed=moderation_dm_embed(
                action="kicked",
                guild_name=interaction.guild.name,
                moderator=interaction.user,
                reason=reason,
            )
        )
    except discord.DiscordException:
        logger.info("Could not DM %s before kick.", member)

    await member.kick(reason=f"{reason} | Moderator: {interaction.user}")
    await interaction.response.send_message(
        embed=build_action_embed(
            title="Member Kicked",
            description=f"{member.mention} has been kicked.",
            reason=reason,
        ),
        ephemeral=True,
    )


@app_commands.describe(member="Member to ban", reason="Reason for the ban")
@app_commands.default_permissions(ban_members=True)
async def ban_member(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided.",
) -> None:
    if interaction.guild is None or interaction.user is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    if member == interaction.user:
        await interaction.response.send_message(
            "You cannot ban yourself.",
            ephemeral=True,
        )
        return

    moderator = interaction.user if isinstance(interaction.user, discord.Member) else None
    bot_member = get_guild_bot_member(interaction.guild)

    if moderator and member.top_role >= moderator.top_role and interaction.guild.owner_id != moderator.id:
        await interaction.response.send_message(
            "You cannot ban that member because their role is higher than or equal to yours.",
            ephemeral=True,
        )
        return

    if bot_member is None or member.top_role >= bot_member.top_role:
        await interaction.response.send_message(
            "I cannot ban that member because their role is higher than or equal to mine.",
            ephemeral=True,
        )
        return

    try:
        await member.send(
            embed=moderation_dm_embed(
                action="banned",
                guild_name=interaction.guild.name,
                moderator=interaction.user,
                reason=reason,
            )
        )
    except discord.DiscordException:
        logger.info("Could not DM %s before ban.", member)

    await member.ban(reason=f"{reason} | Moderator: {interaction.user}")
    await interaction.response.send_message(
        embed=build_action_embed(
            title="Member Banned",
            description=f"{member.mention} has been banned.",
            reason=reason,
        ),
        ephemeral=True,
    )


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")


def create_bot() -> NHSBot:
    new_bot = NHSBot()
    new_bot.event(on_ready)
    new_bot.event(on_member_join)
    new_bot.tree.command(
        name="verification",
        description="Send the verification embed again.",
    )(verification)
    new_bot.tree.command(
        name="kick",
        description="Kick a member from the server.",
    )(kick_member)
    new_bot.tree.command(
        name="ban",
        description="Ban a member from the server.",
    )(ban_member)
    return new_bot


async def run_bot_forever() -> None:
    global bot
    backoff = 15
    while True:
        try:
            bot = create_bot()
            await bot.start(TOKEN)
            return
        except discord.HTTPException as exc:
            logger.error("Discord login failed with HTTP %s: %s", exc.status, exc)
            if is_cloudflare_rate_limit_error(exc):
                backoff = max(backoff, 900)
                logger.error(
                    "Discord is blocking this host IP via Cloudflare 1015. "
                    "This is an infrastructure issue with the current host, not your bot token or commands."
                )
        except discord.LoginFailure as exc:
            logger.error("Discord login failed: %s", exc)
            raise
        except Exception:
            logger.exception("Bot crashed unexpectedly during startup/runtime.")
        finally:
            if bot is not None:
                await bot.close()
                bot = None

        logger.warning("Retrying Discord connection in %s seconds.", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)


threading.Thread(target=start_healthcheck_server, daemon=True).start()
asyncio.run(run_bot_forever())
