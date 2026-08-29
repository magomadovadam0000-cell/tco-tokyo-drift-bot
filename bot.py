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

# --- IDENTIFIANTS DU SERVEUR ---
LEADERBOARD_CHANNEL_ID = 1543274008311763044
TICKET_CHANNEL_ID = 1543274172321370303
TICKET_CATEGORY_ID = 1543383561103474839
LOGS_CATEGORY_ID = 1543383625456812102

# Dictionnaire pour le cooldown des tickets (60 sec)
user_cooldowns = {}

# --- BASE DE DONNÉES SQLITE (Leaderboard) ---
def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    # Table des scores (victoires / points)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            score INTEGER DEFAULT 0
        )
    """)
    # Table de configuration (ID du message du leaderboard)
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
# 1. SYSTÈME DE TICKETS (Help & Report)
# ==========================================

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.secondary, custom_id="ticket_close_btn", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Restriction : Modérateurs ou Admins uniquement
        if not (interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("❌ Only moderators and server owners can close tickets.", ephemeral=True)
            return

        logs_category = interaction.guild.get_channel(LOGS_CATEGORY_ID)
        if logs_category:
            await interaction.channel.edit(category=logs_category, sync_permissions=False)
            
            # Retire l'accès aux membres normaux
            for target, overwrite in interaction.channel.overwrites.items():
                if isinstance(target, discord.Member) and not target.guild_permissions.manage_channels:
                    await interaction.channel.set_permissions(target, overwrite=None)

            embed = discord.Embed(
                title="🔒 Ticket Closed",
                description=f"This ticket was closed by {interaction.user.mention} and archived in **Ticket Logs**.",
                color=discord.Color.gold(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("❌ Ticket Logs category not found.", ephemeral=True)

    @discord.ui.button(label="Delete Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_delete_btn", emoji="🗑️")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Restriction : Owner du serveur uniquement
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

        # 1. Cooldown de 60 secondes
        if user.id in user_cooldowns:
            elapsed = (now - user_cooldowns[user.id]).total_seconds()
            if elapsed < 60:
                remaining = int(60 - elapsed)
                await interaction.response.send_message(f"⏳ Please wait {remaining} seconds before opening another ticket.", ephemeral=True)
                return

        # 2. Vérification si ticket déjà actif
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
# 2. GESTION DU LEADERBOARD AUTOMATIQUE
# ==========================================

async def update_leaderboard_message(guild):
    """Met à jour le message d'affichage du classement dans le salon dédié"""
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Récupérer les 10 meilleurs scores
    cursor.execute("SELECT username, score FROM leaderboard ORDER BY score DESC LIMIT 10")
    top_scores = cursor.fetchall()

    # Récupérer l'ID du message stocké en DB
    cursor.execute("SELECT value FROM config WHERE key = 'lb_message_id'")
    row = cursor.fetchone()
    message_id = row[0] if row else None
    conn.close()

    channel = guild.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel:
        return

    # Construction du tableau de score
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

    # Mettre à jour le message s'il existe déjà
    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed)
            return
        except discord.NotFound:
            pass

    # Si pas de message existant, en créer un nouveau
    new_msg = await channel.send(embed=embed)
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('lb_message_id', ?)", (new_msg.id,))
    conn.commit()
    conn.close()


# ==========================================
# 3. COMMANDES SLASH (ALL IN SLASH)
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


# --- 1. /setup_ticket (OWNER ONLY) ---
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


# --- 2. /setup_leaderboard (OWNER ONLY) ---
@bot.tree.command(name="setup_leaderboard", description="Initialiser le message du leaderboard (Owner uniquement).")
async def setup_leaderboard(interaction: discord.Interaction):
    if interaction.user != interaction.guild.owner:
        await interaction.response.send_message("❌ Only the server Owner can use this command.", ephemeral=True)
        return

    await interaction.response.send_message("⏳ Setting up leaderboard...", ephemeral=True)
    await update_leaderboard_message(interaction.guild)


# --- 3. /add_win (OWNER ONLY) ---
@bot.tree.command(name="add_win", description="Ajouter des victoires/points à un joueur (Owner uniquement).")
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
    await update_leaderboard_message(interaction.guild)


# --- 4. /remove_win (OWNER ONLY) ---
@bot.tree.command(name="remove_win", description="Retirer des victoires/points à un joueur (Owner uniquement).")
async def remove_win(interaction: discord.Interaction, member: discord.Member, amount: int = 1):
    if interaction.user != interaction.guild.owner:
        await interaction.response.send_message("❌ Only the server Owner can manage wins.", ephemeral=True)
        return

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT score FROM leaderboard WHERE user_id = ?", (member.id,))
    row = cursor.fetchone()
    current_score = row[0] if row else 0
    new_score = max(0, current_score - amount)  # Empêche d'avoir un score négatif

    cursor.execute("INSERT OR REPLACE INTO leaderboard (user_id, username, score) VALUES (?, ?, ?)",
                   (member.id, member.display_name, new_score))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ Removed `{amount}` win(s) from **{member.display_name}**. Total: `{new_score}`", ephemeral=True)
    await update_leaderboard_message(interaction.guild)


# LANCEMENT DU BOT
load_dotenv()
bot.run(os.getenv("DISCORD_TOKEN"))