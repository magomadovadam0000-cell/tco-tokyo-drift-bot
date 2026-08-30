import discord
from discord.ext import commands
import sqlite3
import datetime
import os
from dotenv import load_dotenv

# --- CONFIGURATION INITIALE & INTENTS ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- IDENTIFIANTS DU SERVEUR & SALONS ---
LEADERBOARD_CHANNEL_ID = 1543274008311763044
TICKET_CHANNEL_ID = 1543274172321370303
TICKET_CATEGORY_ID = 1543383561103474839
LOGS_CATEGORY_ID = 1543383625456812102
ANNOUNCEMENT_CHANNEL_ID = 1543273382659887175  # Announcement
COMMAND_LOGS_CHANNEL_ID = 1543274576803405965  # Staff > Logs

# 🏆 CONFIGURATION DES 5 RÔLES DRIFT AVEC LEURS ID DIRECTS
ROLE_IDS = {
    "ROOKIE": 1543330837804744704,
    "STREET": 1543331085163958302,
    "PRO": 1543331190038204567,
    "TRACKER": 1543331274704560210,  # Track
    "KING": 1543331441952301108
}

# Paliers de victoires associés aux ID
RANK_THRESHOLDS_BY_ID = [
    (50, "KING"),
    (30, "TRACKER"),
    (15, "PRO"),
    (5, "STREET"),
    (0, "ROOKIE")
]

user_cooldowns = {}

# --- BASE DE DONNÉES SQLITE ---
def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            score INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()


# ==========================================
# ⚙️ GESTION AUTOMATIQUE DES RÔLES PAR ID
# ==========================================

async def check_and_update_driver_role(guild: discord.Guild, member: discord.Member, new_score: int):
    """Détermine le rôle par son ID et l'attribue au membre."""
    target_key = "ROOKIE"
    for threshold, key in RANK_THRESHOLDS_BY_ID:
        if new_score >= threshold:
            target_key = key
            break

    target_role_id = ROLE_IDS.get(target_key)
    target_role = guild.get_role(target_role_id)

    if not target_role:
        print(f"⚠️ Rôle avec l'ID {target_role_id} introuvable sur le serveur.")
        return

    # Si le joueur a déjà ce rôle, on ne fait rien
    if target_role in member.roles:
        return

    # Retirer les autres rôles de pilote
    all_driver_role_ids = list(ROLE_IDS.values())
    roles_to_remove = [r for r in member.roles if r.id in all_driver_role_ids and r.id != target_role_id]

    if roles_to_remove:
        try:
            await member.remove_roles(*roles_to_remove)
        except Exception as e:
            print(f"Erreur lors du retrait des anciens rôles : {e}")

    # Attribuer le nouveau rôle et envoyer l'annonce
    try:
        await member.add_roles(target_role)

        announcement_channel = guild.get_channel(ANNOUNCEMENT_CHANNEL_ID)
        if announcement_channel:
            embed = discord.Embed(
                title="🏎️ NEW DRIVER RANK ACHIEVED!",
                description=f"Congratulations {member.mention}! You have officially reached **{new_score} Wins** and unlocked the **{target_role.name}** rank! 🎉",
                color=discord.Color.gold(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="TCO Drift Driver Progression")
            await announcement_channel.send(embed=embed)
    except Exception as e:
        print(f"Erreur lors de l'ajout du rôle {target_role.name} : {e}")


# ==========================================
# 1. LOGS AUTOMATIQUES DES COMMANDES EXÉCUTÉES
# ==========================================

@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: discord.app_commands.Command):
    log_channel = interaction.guild.get_channel(COMMAND_LOGS_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(
            title="📜 COMMAND LOG ENTRY",
            description=f"**Executor:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                        f"**Command Used:** `/{command.name}`\n"
                        f"**Channel:** {interaction.channel.mention}",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        if interaction.data.get("options"):
            opts = [f"`{opt['name']}`: {opt['value']}" for opt in interaction.data["options"]]
            embed.add_field(name="Arguments", value="\n".join(opts), inline=False)

        await log_channel.send(embed=embed)


# ==========================================
# 2. SYSTÈME DE TICKETS (Help & Report)
# ==========================================

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.secondary, custom_id="ticket_close_btn", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("❌ Only moderators and server owners can close tickets.", ephemeral=True)
            return

        logs_category = interaction.guild.get_channel(LOGS_CATEGORY_ID)
        if logs_category:
            await interaction.channel.edit(category=logs_category, sync_permissions=False)

            for target, overwrite in interaction.channel.overwrites.items():
                if isinstance(target, discord.Member) and not target.guild_permissions.manage_channels:
                    await interaction.channel.set_permissions(target, overwrite=None)

            embed = discord.Embed(
                title="🔒 Ticket Closed & Archived",
                description=f"This ticket was closed by {interaction.user.mention} and archived.",
                color=discord.Color.gold(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            await interaction.response.send_message(embed=embed)

            log_channel = interaction.guild.get_channel(COMMAND_LOGS_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title="📌 TICKET ARCHIVED LOG",
                    description=f"**Ticket Channel:** {interaction.channel.name}\n**Closed By:** {interaction.user.mention}",
                    color=discord.Color.dark_grey(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                await log_channel.send(embed=log_embed)
        else:
            await interaction.response.send_message("❌ Ticket Logs category not found.", ephemeral=True)

    @discord.ui.button(label="Delete Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_delete_btn", emoji="🗑️")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != interaction.guild.owner:
            await interaction.response.send_message("❌ Only the server Owner can delete tickets.", ephemeral=True)
            return

        await interaction.response.send_message("🗑️ Deleting ticket in 5 seconds...")
        await discord.utils.sleep_until(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=5))
        await interaction.channel.delete()


class TicketDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Help", value="help", description="General support, technical assistance, or questions.", emoji="❓"),
            discord.SelectOption(label="Report", value="report", description="Report a rule violation or player misconduct.", emoji="🛡️")
        ]
        super().__init__(
            placeholder="Select a category to open a ticket...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_dropdown_select"
        )

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        now = datetime.datetime.now(datetime.timezone.utc)

        if user.id in user_cooldowns:
            elapsed = (now - user_cooldowns[user.id]).total_seconds()
            if elapsed < 60:
                remaining = int(60 - elapsed)
                await interaction.response.send_message(f"⏳ Please wait {remaining} seconds before opening another ticket.", ephemeral=True)
                return

        ticket_category = guild.get_channel(TICKET_CATEGORY_ID)
        if ticket_category:
            for channel in ticket_category.text_channels:
                if channel.name.endswith(f"-{user.id}") or channel.name.startswith(f"{self.values[0]}-{user.name.lower()}"):
                    await interaction.response.send_message("❌ You already have an active ticket open!", ephemeral=True)
                    return

        user_cooldowns[user.id] = now
        selected_option = self.values[0]
        channel_name = f"{selected_option}-{user.name.lower()}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=ticket_category,
            overwrites=overwrites,
            reason=f"Ticket opened by {user}"
        )

        await interaction.response.send_message(f"✅ Your ticket has been created: {ticket_channel.mention}", ephemeral=True)

        embed = discord.Embed(
            title=f"Ticket: {selected_option.upper()} — {user.display_name}",
            description=f"Welcome {user.mention}!\n\nPlease explain your issue or report in detail. Staff will assist you shortly.",
            color=discord.Color.red() if selected_option == "report" else discord.Color.blue(),
            timestamp=now
        )
        embed.set_footer(text="TCO Drift Ticket System")

        await ticket_channel.send(embed=embed, view=TicketControlView())


class TicketDropdownView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())


# ==========================================
# 3. GESTION DU LEADERBOARD AUTOMATIQUE
# ==========================================

async def update_leaderboard_message(guild):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, score FROM leaderboard ORDER BY score DESC LIMIT 10")
    top_scores = cursor.fetchall()

    cursor.execute("SELECT value FROM config WHERE key = 'lb_message_id'")
    row = cursor.fetchone()
    message_id = row[0] if row else None
    conn.close()

    channel = guild.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(
        title="🏆 TCO DRIFT — OFFICIAL LEADERBOARD",
        description="Official driver standings updated by Server Owner.",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )

    if not top_scores:
        embed.add_field(name="Standings", value="No wins registered yet.", inline=False)
    else:
        lb_text = ""
        medals = ["🥇", "🥈", "🥉"]
        for idx, (username, score) in enumerate(top_scores, start=1):
            prefix = medals[idx-1] if idx <= 3 else f"`#{idx}`"
            lb_text += f"{prefix} **{username}** — `{score:,}` Wins\n"
        embed.add_field(name="Top Drivers", value=lb_text, inline=False)

    embed.set_footer(text="Auto-updated by TCO Drift Bot")

    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed)
            return
        except discord.NotFound:
            pass

    new_msg = await channel.send(embed=embed)
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('lb_message_id', ?)", (new_msg.id,))
    conn.commit()
    conn.close()


# ==========================================
# 4. COMMANDES SLASH (OWNER ONLY)
# ==========================================

@bot.event
async def on_ready():
    bot.add_view(TicketDropdownView())
    bot.add_view(TicketControlView())
    try:
        synced = await bot.tree.sync()
        print(f"OK ! {len(synced)} commande(s) Slash synchronisée(s) globalement.")
    except Exception as e:
        print(f"Erreur de synchro : {e}")
    print(f"Connecté en tant que : {bot.user}")


@bot.tree.command(name="setup_ticket", description="Afficher le panneau de ticket (Owner uniquement).")
async def setup_ticket(interaction: discord.Interaction):
    if interaction.user != interaction.guild.owner:
        await interaction.response.send_message("❌ Only the server Owner can use this command.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🏎️ TCO DRIFT — SUPPORT & TICKETS",
        description=(
            "Need help or want to report a rule violation?\n"
            "Select an option from the drop-down menu below to open a private ticket with our staff.\n\n"
            "📌 **Ticket Rules:**\n"
            "• You can only have **1 active ticket** at a time.\n"
            "• A **60-second cooldown** applies between creations.\n"
            "• Only Moderators and Owners can close tickets; only Owners can delete them."
        ),
        color=discord.Color.red()
    )
    await interaction.channel.send(embed=embed, view=TicketDropdownView())
    await interaction.response.send_message("✅ Ticket panel posted successfully!", ephemeral=True)


@bot.tree.command(name="setup_leaderboard", description="Initialiser le message du leaderboard (Owner uniquement).")
async def setup_leaderboard(interaction: discord.Interaction):
    if interaction.user != interaction.guild.owner:
        await interaction.response.send_message("❌ Only the server Owner can use this command.", ephemeral=True)
        return

    await interaction.response.send_message("⏳ Setting up leaderboard...", ephemeral=True)
    await update_leaderboard_message(interaction.guild)


@bot.tree.command(name="add_win", description="Ajouter des victoires à un joueur et ajuster son rôle (Owner uniquement).")
async def add_win(interaction: discord.Interaction, member: discord.Member, amount: int = 1):
    if interaction.user != interaction.guild.owner:
        await interaction.response.send_message("❌ Only the server Owner can manage wins.", ephemeral=True)
        return

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT score FROM leaderboard WHERE user_id = ?", (member.id,))
    row = cursor.fetchone()
    current_score = row[0] if row else 0
    new_score = current_score + amount

    cursor.execute("INSERT OR REPLACE INTO leaderboard (user_id, username, score) VALUES (?, ?, ?)",
                   (member.id, member.display_name, new_score))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ Added `{amount}` win(s) to **{member.display_name}**. Total: `{new_score}`", ephemeral=True)

    await check_and_update_driver_role(interaction.guild, member, new_score)
    await update_leaderboard_message(interaction.guild)


@bot.tree.command(name="remove_win", description="Retirer des victoires à un joueur et ajuster son rôle (Owner uniquement).")
async def remove_win(interaction: discord.Interaction, member: discord.Member, amount: int = 1):
    if interaction.user != interaction.guild.owner:
        await interaction.response.send_message("❌ Only the server Owner can manage wins.", ephemeral=True)
        return

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT score FROM leaderboard WHERE user_id = ?", (member.id,))
    row = cursor.fetchone()
    current_score = row[0] if row else 0
    new_score = max(0, current_score - amount)

    cursor.execute("INSERT OR REPLACE INTO leaderboard (user_id, username, score) VALUES (?, ?, ?)",
                   (member.id, member.display_name, new_score))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ Removed `{amount}` win(s) from **{member.display_name}**. Total: `{new_score}`", ephemeral=True)

    await check_and_update_driver_role(interaction.guild, member, new_score)
    await update_leaderboard_message(interaction.guild)


# LANCEMENT DU BOT
load_dotenv()
bot.run(os.getenv("DISCORD_TOKEN"))