import asyncio
import io
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

WELCOME_CHANNEL_ID = 1495896980935806996
VERIFICATION_CHANNEL_ID = 1486873233377460437
RULES_CHANNEL_ID = 1486119499685167204
APPLY_CHANNEL_ID = 1485793827963932682
SUPPORT_CHANNEL_ID = 1485794545261219922
TICKET_OPEN_CATEGORY_ID = 1485776062515253298
SUPPORT_TEAM_ROLE_ID = 1488213002430971914
TICKET_LOG_CHANNEL_ID = 1496130508458037359
VERIFY_URL = "https://melonly.xyz/verify/7451655967730569216/7451709577638187008"

ESCALATION_CATEGORIES = {
    "Hospital | Genaral Support": 1488214977163690157,
    "Hospital | Hospital Management": 1488215094302081207,
    "Hospital | Executive Board": 1488215172794290176,
    "NWAS | Genaral Support": 1487612725801648198,
    "NWAS | Hospital": 1488298415396880416,
    "NWAS | Gold Command": 1488609447491407953,
}

EMBED_COLOR = discord.Color.from_rgb(22, 163, 74)
ALERT_COLOR = discord.Color.red()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("nhs-bot")

BASE_DIR = Path(__file__).resolve().parent
PHOTOS_DIR = BASE_DIR / "Photos"
TOP_IMAGE_PATH = PHOTOS_DIR / "Support Embed Top Image.png"
APPLICATION_TOP_IMAGE_PATH = PHOTOS_DIR / "Application Embed Top Image.png"
BOTTOM_IMAGE_PATH = PHOTOS_DIR / "Embed Bottom Image.png"
SUPPORT_PANEL_TITLE = "NHS Support"
TICKET_TOPIC_PREFIX = "ticket_owner:"
TICKET_REASON_PREFIX = "reason:"
HOSPITAL_APPLICATION_URL = "https://melon.ly/form/7452110262565343232"
NWAS_APPLICATION_URL = "https://melon.ly/form/7452116959954472960"


def is_cloudflare_rate_limit_error(exc: discord.HTTPException) -> bool:
    response_text = str(exc)
    return exc.status == 429 and ("Error 1015" in response_text or "Cloudflare" in response_text)


def is_global_login_rate_limit_error(exc: discord.HTTPException) -> bool:
    response_text = str(exc)
    return exc.status == 429 and "exceeding global rate limits" in response_text


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


class OpenTicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Support Ticket",
        style=discord.ButtonStyle.success,
        custom_id="nhs:open_support_ticket",
    )
    async def open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button

        await interaction.response.send_modal(OpenTicketModal())


class ApplicationView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="🏥 Application",
                url=HOSPITAL_APPLICATION_URL,
                style=discord.ButtonStyle.link,
                row=0,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="🚑 Application",
                url=NWAS_APPLICATION_URL,
                style=discord.ButtonStyle.link,
                row=1,
            )
        )


class OpenTicketModal(discord.ui.Modal, title="Open Support Ticket"):
    help_reason = discord.ui.TextInput(
        label="What can we help you with today?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
        placeholder="Please explain your issue or what you need help with.",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Tickets can only be opened inside the server.",
                ephemeral=True,
            )
            return

        category = interaction.guild.get_channel(TICKET_OPEN_CATEGORY_ID)
        if category is None:
            try:
                category = await interaction.guild.fetch_channel(TICKET_OPEN_CATEGORY_ID)
            except discord.DiscordException as exc:
                logger.warning("Could not fetch ticket category: %s", exc)
                await interaction.response.send_message(
                    "I couldn't find the configured ticket category.",
                    ephemeral=True,
                )
                return

        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "The configured ticket category is invalid.",
                ephemeral=True,
            )
            return

        existing_channel = find_existing_ticket_channel(interaction.guild, interaction.user.id)
        if existing_channel is not None:
            await interaction.response.send_message(
                f"You already have an open ticket: {existing_channel.mention}",
                ephemeral=True,
            )
            return

        ticket_name = build_ticket_channel_name(interaction.user)
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }

        support_role = interaction.guild.get_role(SUPPORT_TEAM_ROLE_ID)
        if support_role is not None:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                attach_files=True,
                embed_links=True,
            )

        bot_member = get_guild_bot_member(interaction.guild)
        if bot_member is not None:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            )

        try:
            channel = await interaction.guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites,
                topic=build_ticket_topic(interaction.user.id, str(self.help_reason)),
                reason=f"Support ticket opened by {interaction.user} ({interaction.user.id})",
            )
        except discord.DiscordException as exc:
            logger.warning("Could not create ticket channel: %s", exc)
            await interaction.response.send_message(
                "I couldn't create your ticket right now.",
                ephemeral=True,
            )
            return

        mention_prefix = interaction.user.mention
        if support_role is not None:
            mention_prefix = f"{mention_prefix} {support_role.mention}"

        await interaction.response.send_message(
            f"Your ticket has been created: {channel.mention}",
            ephemeral=True,
        )
        await channel.send(
            content=mention_prefix,
            embed=ticket_created_embed(interaction.user, interaction.guild.name, str(self.help_reason)),
            view=TicketControlsView(),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )


class EscalateCategorySelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label=name, value=str(category_id))
            for name, category_id in ESCALATION_CATEGORIES.items()
        ]
        super().__init__(
            placeholder="Select the category to move this ticket into",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="nhs:ticket_escalation_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This can only be used inside a ticket channel.",
                ephemeral=True,
            )
            return

        if not user_has_support_role(interaction):
            await interaction.response.send_message(
                "Only the support team can escalate tickets.",
                ephemeral=True,
            )
            return

        category_id = int(self.values[0])
        category = interaction.guild.get_channel(category_id)
        if category is None:
            try:
                category = await interaction.guild.fetch_channel(category_id)
            except discord.DiscordException as exc:
                logger.warning("Could not fetch escalation category %s: %s", category_id, exc)
                await interaction.response.send_message(
                    "I couldn't find that category.",
                    ephemeral=True,
                )
                return

        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "That destination is not a valid category.",
                ephemeral=True,
            )
            return

        try:
            await interaction.channel.edit(
                category=category,
                reason=f"Ticket escalated by {interaction.user} to {category.name}",
            )
        except discord.DiscordException as exc:
            logger.warning("Could not move ticket channel: %s", exc)
            await interaction.response.send_message(
                "I couldn't move this ticket right now.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Ticket moved to **{category.name}**.",
            ephemeral=True,
        )
        await interaction.channel.send(
            embed=build_action_embed(
                title="Ticket Escalated",
                description=f"This ticket has been moved to **{category.name}**.",
                reason=f"Escalated by {interaction.user}",
            )
        )


class EscalateView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=180)
        self.add_item(EscalateCategorySelect())


class TicketControlsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="nhs:close_ticket",
    )
    async def close_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button

        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This button only works inside a ticket channel.",
                ephemeral=True,
            )
            return

        if not user_has_support_role(interaction):
            await interaction.response.send_message(
                "Only the support team can close tickets.",
                ephemeral=True,
            )
            return

        if get_ticket_owner_id(interaction.channel) is None:
            await interaction.response.send_message(
                "This button only works inside a ticket channel.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(CloseTicketModal())


class CloseTicketModal(discord.ui.Modal, title="Close Ticket"):
    close_reason = discord.ui.TextInput(
        label="Reason for closing this ticket",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
        placeholder="Explain why this ticket is being closed.",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This can only be used in a ticket channel.",
                ephemeral=True,
            )
            return

        await close_ticket_channel(interaction, str(self.close_reason))


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
        self.add_view(OpenTicketView())
        self.add_view(TicketControlsView())
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
        title="Community Verification & Whitelist",
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
            "1. Click the Verify Now button below.\n"
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
        title="Welcome to National Health Service",
        description=f"Hello, {member.mention}, you've checked into reception please take a seat in the waiting area!",
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Get Started",
        value=(
            f"• Verify in <#{VERIFICATION_CHANNEL_ID}>\n"
            f"• Read <#{RULES_CHANNEL_ID}>"
        ),
        inline=False,
    )
    embed.add_field(
        name="Want to Join Us?",
        value=f"Apply in <#{APPLY_CHANNEL_ID}>",
        inline=False,
    )
    embed.add_field(
        name="Need Help?",
        value=f"Visit <#{SUPPORT_CHANNEL_ID}>",
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


def support_panel_embeds() -> list[discord.Embed]:
    panel_embed = discord.Embed(
        title="NHS Support",
        description="```Open a support ticket and It will be escalated to the required\ndepartment and people.```",
        color=discord.Color.from_rgb(49, 51, 56),
        timestamp=datetime.now(timezone.utc),
    )
    panel_embed.set_image(url="attachment://top-image.png")
    panel_embed.set_footer(text="National Health Service Support")

    banner_embed = discord.Embed(color=discord.Color.from_rgb(49, 51, 56))
    banner_embed.set_image(url="attachment://bottom-image.png")
    return [panel_embed, banner_embed]


def application_panel_embeds() -> list[discord.Embed]:
    panel_embed = discord.Embed(
        description=(
            "** # `🏥`Hospital Staff Application **\n"
            "*Step in, make a difference, save lives.*\n\n"
            "──────────────\n\n"
            "** # `🚑` NWAS Application **\n"
            "*Every second counts—be the one who makes it count.*"
        ),
        color=discord.Color.from_rgb(49, 51, 56),
        timestamp=datetime.now(timezone.utc),
    )
    panel_embed.set_image(url="attachment://application-top-image.png")

    separator_embed = discord.Embed(
        description="──────────────",
        color=discord.Color.from_rgb(49, 51, 56),
    )

    banner_embed = discord.Embed(color=discord.Color.from_rgb(49, 51, 56))
    banner_embed.set_image(url="attachment://bottom-image.png")
    return [panel_embed, separator_embed, banner_embed]


def ticket_created_embed(member: discord.Member, guild_name: str, issue: str) -> discord.Embed:
    embed = discord.Embed(
        title="Support Ticket Opened",
        description=(
            f"{member.mention}, thanks for opening a support ticket in **{guild_name}**.\n"
            "A member of the support team will be with you shortly."
        ),
        color=EMBED_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Issue",
        value=issue,
        inline=False,
    )
    embed.add_field(
        name="What to include",
        value="Please explain your issue clearly and include any useful screenshots or evidence.",
        inline=False,
    )
    embed.set_footer(text="Support can use /esculate or the close button below.")
    return embed


def support_panel_files() -> list[discord.File]:
    files: list[discord.File] = []
    if TOP_IMAGE_PATH.exists():
        files.append(discord.File(str(TOP_IMAGE_PATH), filename="top-image.png"))
    if BOTTOM_IMAGE_PATH.exists():
        files.append(discord.File(str(BOTTOM_IMAGE_PATH), filename="bottom-image.png"))
    return files


def application_panel_files() -> list[discord.File]:
    files: list[discord.File] = []
    if APPLICATION_TOP_IMAGE_PATH.exists():
        files.append(
            discord.File(str(APPLICATION_TOP_IMAGE_PATH), filename="application-top-image.png")
        )
    if BOTTOM_IMAGE_PATH.exists():
        files.append(discord.File(str(BOTTOM_IMAGE_PATH), filename="bottom-image.png"))
    return files


def build_ticket_channel_name(user: discord.abc.User) -> str:
    safe_name = "".join(char.lower() if char.isalnum() else "-" for char in user.name)
    collapsed = "-".join(part for part in safe_name.split("-") if part)
    base = collapsed[:70] if collapsed else f"user-{user.id}"
    return f"ticket-{base}"


def user_has_support_role(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    return any(role.id == SUPPORT_TEAM_ROLE_ID for role in interaction.user.roles)


def get_ticket_owner_id(channel: discord.TextChannel) -> int | None:
    if channel.topic:
        owner_segment = channel.topic.split("|", maxsplit=1)[0].strip()
        if owner_segment.startswith(TICKET_TOPIC_PREFIX):
            owner_id = owner_segment.removeprefix(TICKET_TOPIC_PREFIX).strip()
            if owner_id.isdigit():
                return int(owner_id)
    return None


def get_ticket_issue(channel: discord.TextChannel) -> str | None:
    if channel.topic and "|" in channel.topic:
        _, remainder = channel.topic.split("|", maxsplit=1)
        issue_segment = remainder.strip()
        if issue_segment.startswith(TICKET_REASON_PREFIX):
            return issue_segment.removeprefix(TICKET_REASON_PREFIX).strip() or None
    return None


def build_ticket_topic(user_id: int, issue: str) -> str:
    normalized_issue = " ".join(issue.split())
    return f"{TICKET_TOPIC_PREFIX}{user_id} | {TICKET_REASON_PREFIX}{normalized_issue[:900]}"


def find_existing_ticket_channel(guild: discord.Guild, user_id: int) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        if get_ticket_owner_id(channel) == user_id:
            return channel
    return None


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


def ticket_closed_embed(
    channel_name: str,
    guild_name: str,
    closed_by: discord.abc.User,
    reason: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="Support Ticket Closed",
        description=f"Your ticket **{channel_name}** in **{guild_name}** has been closed.",
        color=ALERT_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Closed by", value=f"{closed_by} (`{closed_by.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    return embed


async def build_ticket_transcript(channel: discord.TextChannel) -> io.BytesIO:
    buffer = io.StringIO()
    buffer.write(f"Transcript for #{channel.name}\n")
    buffer.write(f"Guild: {channel.guild.name}\n")
    owner_id = get_ticket_owner_id(channel)
    if owner_id is not None:
        buffer.write(f"Ticket owner: {owner_id}\n")
    issue = get_ticket_issue(channel)
    if issue:
        buffer.write(f"Opened for: {issue}\n")
    buffer.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
    buffer.write("=" * 80 + "\n\n")

    messages: list[discord.Message] = []
    async for message in channel.history(limit=None, oldest_first=True):
        messages.append(message)

    for message in messages:
        created = message.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{message.author} ({message.author.id})"
        content = message.content or "[no text content]"
        buffer.write(f"[{created}] {author}\n")
        buffer.write(f"{content}\n")

        if message.embeds:
            for index, embed in enumerate(message.embeds, start=1):
                embed_title = embed.title or "Untitled Embed"
                embed_description = embed.description or "[no description]"
                buffer.write(f"[Embed {index}] {embed_title}\n{embed_description}\n")

        if message.attachments:
            for attachment in message.attachments:
                buffer.write(f"[Attachment] {attachment.url}\n")

        buffer.write("\n")

    data = io.BytesIO(buffer.getvalue().encode("utf-8"))
    data.seek(0)
    return data


async def close_ticket_channel(interaction: discord.Interaction, reason: str) -> None:
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "This can only be used inside a ticket channel.",
            ephemeral=True,
        )
        return

    if not user_has_support_role(interaction):
        await interaction.response.send_message(
            "Only the support team can close tickets.",
            ephemeral=True,
        )
        return

    owner_id = get_ticket_owner_id(interaction.channel)
    if owner_id is None:
        await interaction.response.send_message(
            "This command only works inside a ticket channel.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        "Closing ticket and sending transcript...",
        ephemeral=True,
    )

    transcript_bytes = await build_ticket_transcript(interaction.channel)
    transcript_name = f"{interaction.channel.name}-transcript.txt"
    owner_member = interaction.guild.get_member(owner_id)
    if owner_member is None:
        try:
            owner_member = await interaction.guild.fetch_member(owner_id)
        except discord.DiscordException:
            owner_member = None

    if owner_member is not None:
        try:
            owner_file = discord.File(io.BytesIO(transcript_bytes.getvalue()), filename=transcript_name)
            await owner_member.send(
                embed=ticket_closed_embed(
                    interaction.channel.name,
                    interaction.guild.name,
                    interaction.user,
                    reason,
                ),
                file=owner_file,
            )
        except discord.DiscordException:
            logger.info("Could not DM transcript to ticket owner %s.", owner_id)

    log_channel = interaction.guild.get_channel(TICKET_LOG_CHANNEL_ID)
    if log_channel is None:
        try:
            log_channel = await interaction.guild.fetch_channel(TICKET_LOG_CHANNEL_ID)
        except discord.DiscordException:
            log_channel = None

    if isinstance(log_channel, discord.TextChannel):
        log_file = discord.File(io.BytesIO(transcript_bytes.getvalue()), filename=transcript_name)
        log_embed = ticket_closed_embed(
            interaction.channel.name,
            interaction.guild.name,
            interaction.user,
            reason,
        )
        if owner_member is not None:
            log_embed.add_field(name="Ticket owner", value=owner_member.mention, inline=False)
        await log_channel.send(embed=log_embed, file=log_file)

    await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}: {reason}")


async def ensure_verification_message() -> None:
    if bot is None or bot.verification_message_checked:
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
                and message.embeds[0].title == "Community Verification & Whitelist"
            ):
                bot.verification_message_checked = True
                return
    except discord.DiscordException as exc:
        logger.warning("Could not inspect verification channel history: %s", exc)

    await bot.queue_message(channel, embed=verification_embed(), view=VerificationView())
    bot.verification_message_checked = True
    logger.info("Posted verification message in #%s.", channel.name)


async def ensure_support_panel_message() -> None:
    return


async def on_ready() -> None:
    if bot is None or bot.user is None:
        return
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)
    await ensure_verification_message()


async def on_member_join(member: discord.Member) -> None:
    if bot is None:
        return

    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        try:
            channel = await member.guild.fetch_channel(WELCOME_CHANNEL_ID)
        except discord.DiscordException as exc:
            logger.warning("Could not fetch welcome channel: %s", exc)
            return

    if not isinstance(channel, discord.TextChannel):
        logger.warning("Welcome channel is not a text channel.")
        return

    await bot.queue_message(
        channel,
        content=member.mention,
        embed=welcome_embed(member),
        allowed_mentions=discord.AllowedMentions(users=True),
    )


async def verification(interaction: discord.Interaction) -> None:
    if interaction.channel is None or bot is None:
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


async def support(interaction: discord.Interaction) -> None:
    if bot is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    channel = interaction.guild.get_channel(SUPPORT_CHANNEL_ID) if interaction.guild else None
    if channel is None and interaction.guild is not None:
        try:
            channel = await interaction.guild.fetch_channel(SUPPORT_CHANNEL_ID)
        except discord.DiscordException as exc:
            logger.warning("Could not fetch support channel: %s", exc)
            channel = None

    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "I couldn't find the configured support channel.",
            ephemeral=True,
        )
        return

    payload = {
        "embeds": support_panel_embeds(),
        "view": OpenTicketView(),
    }
    files = support_panel_files()
    if files:
        payload["files"] = files
    await bot.queue_message(channel, **payload)
    await interaction.response.send_message(
        f"Support panel sent in {channel.mention}.",
        ephemeral=True,
    )


async def application(interaction: discord.Interaction) -> None:
    if bot is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    channel = interaction.guild.get_channel(APPLY_CHANNEL_ID) if interaction.guild else None
    if channel is None and interaction.guild is not None:
        try:
            channel = await interaction.guild.fetch_channel(APPLY_CHANNEL_ID)
        except discord.DiscordException as exc:
            logger.warning("Could not fetch application channel: %s", exc)
            channel = None

    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "I couldn't find the configured application channel.",
            ephemeral=True,
        )
        return

    payload = {
        "embeds": application_panel_embeds(),
        "view": ApplicationView(),
    }
    files = application_panel_files()
    if files:
        payload["files"] = files
    await bot.queue_message(channel, **payload)
    await interaction.response.send_message(
        f"Application panel sent in {channel.mention}.",
        ephemeral=True,
    )


@app_commands.default_permissions(manage_guild=True)
async def support_command(interaction: discord.Interaction) -> None:
    await support(interaction)


@app_commands.default_permissions(manage_guild=True)
async def application_command(interaction: discord.Interaction) -> None:
    await application(interaction)


async def esculate(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "This command can only be used in a server ticket channel.",
            ephemeral=True,
        )
        return

    if not user_has_support_role(interaction):
        await interaction.response.send_message(
            "Only the support team can use this command.",
            ephemeral=True,
        )
        return

    if get_ticket_owner_id(interaction.channel) is None:
        await interaction.response.send_message(
            "This command only works inside a ticket channel.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        "Choose the category you want to move this ticket to.",
        view=EscalateView(),
        ephemeral=True,
    )


@app_commands.default_permissions()
async def esculate_command(interaction: discord.Interaction) -> None:
    await esculate(interaction)


@app_commands.describe(reason="Reason for closing the ticket")
@app_commands.default_permissions()
async def close(interaction: discord.Interaction, reason: str) -> None:
    await close_ticket_channel(interaction, reason)


@app_commands.describe(member="Member to kick", reason="Reason for the kick")
@app_commands.default_permissions(administrator=True)
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
    if moderator is None or not moderator.guild_permissions.administrator:
        await interaction.response.send_message(
            "Only server administrators can use this command.",
            ephemeral=True,
        )
        return

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
@app_commands.default_permissions(administrator=True)
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
    if moderator is None or not moderator.guild_permissions.administrator:
        await interaction.response.send_message(
            "Only server administrators can use this command.",
            ephemeral=True,
        )
        return

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
        name="support",
        description="Send the support ticket panel.",
    )(support_command)
    new_bot.tree.command(
        name="application",
        description="Send the applications panel.",
    )(application_command)
    new_bot.tree.command(
        name="esculate",
        description="Move a ticket into a different escalation category.",
    )(esculate_command)
    new_bot.tree.command(
        name="close",
        description="Close a ticket and send the transcript.",
    )(close)
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
            elif is_global_login_rate_limit_error(exc):
                backoff = max(backoff, 1800)
                logger.error(
                    "Discord is temporarily blocking login from this host due to global rate limits. "
                    "This is a hosting/IP-level issue rather than a bug in the bot commands."
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
        backoff = min(backoff * 2, 3600)


threading.Thread(target=start_healthcheck_server, daemon=True).start()
asyncio.run(run_bot_forever())
