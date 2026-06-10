import logging
import sqlite3
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

# ========== CONFIG ==========
TOKEN = "8346074021:AAHmYoCI-PUo4xUYoJMUSUOKgzl6Ku3aOvI"
OWNER_ID = 8579186775  # Your numeric ID

# ========== DATABASE ==========
conn = sqlite3.connect('catcher.db', check_same_thread=False)
c = conn.cursor()

# Characters table (supports photo and video)
c.execute('''CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    rank TEXT,
    anime TEXT,
    event TEXT,
    media_type TEXT,
    media_file_id TEXT,
    added_by INTEGER
)''')

# Users table
c.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    joined DATE,
    nex_balance INTEGER DEFAULT 0
)''')

# Roles table (admin, uploader)
c.execute('''CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER PRIMARY KEY,
    role TEXT CHECK(role IN ('admin', 'uploader')),
    granted_by INTEGER,
    granted_date DATE
)''')

# User's harm with sequential number
c.execute('''CREATE TABLE IF NOT EXISTS user_catches (
    user_id INTEGER,
    catch_number INTEGER,
    character_id INTEGER,
    caught_date DATE,
    is_favorite INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, catch_number),
    FOREIGN KEY(character_id) REFERENCES characters(id)
)''')

# Ranks table
c.execute('''CREATE TABLE IF NOT EXISTS ranks (
    name TEXT PRIMARY KEY,
    chance INTEGER DEFAULT 50,
    sell_price INTEGER DEFAULT 10
)''')

# Default ranks (English)
default_ranks = [('Epic', 25, 200), ('Legendary', 10, 500), ('Mythic', 5, 1200), 
                 ('Exotic', 3, 3000), ('Op', 1, 8000), ('Universe', 0.5, 20000)]
for name, chance, price in default_ranks:
    c.execute("INSERT OR IGNORE INTO ranks (name, chance, sell_price) VALUES (?, ?, ?)", (name, chance, price))

# Timeouts
c.execute('''CREATE TABLE IF NOT EXISTS user_timeouts (
    user_id INTEGER PRIMARY KEY,
    until DATETIME
)''')

# Config (admin code)
c.execute('''CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
)''')

conn.commit()

# ========== HELPER FUNCTIONS ==========
def get_user_role(user_id):
    if user_id == OWNER_ID:
        return 'owner'
    c.execute("SELECT role FROM user_roles WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else None

def is_admin(user_id):
    role = get_user_role(user_id)
    return role in ('admin', 'owner')

def is_uploader(user_id):
    role = get_user_role(user_id)
    return role in ('uploader', 'admin', 'owner')

def add_user_if_not_exists(user_id, first_name, username):
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not c.fetchone():
        c.execute("INSERT INTO users (user_id, first_name, username, joined, nex_balance) VALUES (?, ?, ?, ?, ?)",
                  (user_id, first_name, username, datetime.now().date(), 0))
        conn.commit()

def get_nex_balance(user_id):
    c.execute("SELECT nex_balance FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else 0

def update_nex_balance(user_id, amount):
    c.execute("UPDATE users SET nex_balance = nex_balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def transfer_nex(sender_id, receiver_id, amount):
    if get_nex_balance(sender_id) < amount:
        return False
    update_nex_balance(sender_id, -amount)
    update_nex_balance(receiver_id, amount)
    return True

def is_timeouted(user_id):
    c.execute("SELECT until FROM user_timeouts WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row:
        until = datetime.fromisoformat(row[0])
        if datetime.now() < until:
            return True
        else:
            c.execute("DELETE FROM user_timeouts WHERE user_id=?", (user_id,))
            conn.commit()
    return False

def set_timeout(user_id, minutes):
    until = datetime.now() + timedelta(minutes=minutes)
    c.execute("REPLACE INTO user_timeouts (user_id, until) VALUES (?, ?)", (user_id, until.isoformat()))
    conn.commit()

def remove_timeout(user_id):
    c.execute("DELETE FROM user_timeouts WHERE user_id=?", (user_id,))
    conn.commit()

def get_rank_info(rank_name):
    c.execute("SELECT chance, sell_price FROM ranks WHERE name=?", (rank_name,))
    row = c.fetchone()
    return row if row else (50, 10)

def add_character(name, rank, anime, event, media_type, media_file_id, admin_id):
    try:
        c.execute("INSERT INTO characters (name, rank, anime, event, media_type, media_file_id, added_by) VALUES (?,?,?,?,?,?,?)",
                  (name, rank, anime, event, media_type, media_file_id, admin_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def remove_character(name):
    c.execute("DELETE FROM characters WHERE LOWER(name)=?", (name.lower(),))
    conn.commit()
    return c.rowcount > 0

def get_character_by_name(name):
    c.execute("SELECT id, name, rank, anime, event, media_type, media_file_id FROM characters WHERE LOWER(name)=?", (name.lower(),))
    return c.fetchone()

def get_character_by_id(char_id):
    c.execute("SELECT id, name, rank, anime, event, media_type, media_file_id, added_by FROM characters WHERE id=?", (char_id,))
    return c.fetchone()

def get_all_characters():
    c.execute("SELECT id, name, rank, anime FROM characters")
    return c.fetchall()

def search_characters(query):
    query_lower = f"%{query.lower()}%"
    c.execute("SELECT id, name, rank, anime, event FROM characters WHERE LOWER(name) LIKE ? OR LOWER(anime) LIKE ?", (query_lower, query_lower))
    return c.fetchall()

def user_has_caught(user_id, character_id):
    c.execute("SELECT 1 FROM user_catches WHERE user_id=? AND character_id=?", (user_id, character_id))
    return c.fetchone() is not None

def get_next_catch_number(user_id):
    c.execute("SELECT MAX(catch_number) FROM user_catches WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return (row[0] or 0) + 1

def add_catch(user_id, character_id):
    num = get_next_catch_number(user_id)
    c.execute("INSERT INTO user_catches (user_id, catch_number, character_id, caught_date, is_favorite) VALUES (?,?,?,?,?)",
              (user_id, num, character_id, datetime.now().date(), 0))
    conn.commit()
    return num

def remove_catch_by_number(user_id, catch_number):
    c.execute("SELECT character_id FROM user_catches WHERE user_id=? AND catch_number=?", (user_id, catch_number))
    row = c.fetchone()
    if not row:
        return None
    char_id = row[0]
    c.execute("DELETE FROM user_catches WHERE user_id=? AND catch_number=?", (user_id, catch_number))
    conn.commit()
    return char_id

def get_catch_by_number(user_id, catch_number):
    c.execute("SELECT character_id, is_favorite FROM user_catches WHERE user_id=? AND catch_number=?", (user_id, catch_number))
    row = c.fetchone()
    return row if row else None

def toggle_favorite(user_id, catch_number):
    c.execute("SELECT is_favorite FROM user_catches WHERE user_id=? AND catch_number=?", (user_id, catch_number))
    row = c.fetchone()
    if not row:
        return False
    new_val = 1 if row[0] == 0 else 0
    c.execute("UPDATE user_catches SET is_favorite = ? WHERE user_id=? AND catch_number=?", (new_val, user_id, catch_number))
    conn.commit()
    return new_val == 1  # True if now favorite

def transfer_character_by_number(sender_id, receiver_id, catch_number):
    c.execute("SELECT character_id, is_favorite FROM user_catches WHERE user_id=? AND catch_number=?", (sender_id, catch_number))
    row = c.fetchone()
    if not row:
        return False
    char_id, fav = row
    c.execute("DELETE FROM user_catches WHERE user_id=? AND catch_number=?", (sender_id, catch_number))
    new_num = get_next_catch_number(receiver_id)
    c.execute("INSERT INTO user_catches (user_id, catch_number, character_id, caught_date, is_favorite) VALUES (?,?,?,?,?)",
              (receiver_id, new_num, char_id, datetime.now().date(), fav))
    conn.commit()
    return True

def get_user_harm_with_numbers(user_id):
    c.execute('''SELECT uc.catch_number, c.name, c.rank, c.anime, c.event, uc.caught_date, uc.is_favorite 
                 FROM user_catches uc 
                 JOIN characters c ON uc.character_id = c.id 
                 WHERE uc.user_id = ? 
                 ORDER BY uc.catch_number ASC''', (user_id,))
    return c.fetchall()

def get_total_catches(user_id):
    c.execute("SELECT COUNT(*) FROM user_catches WHERE user_id=?", (user_id,))
    return c.fetchone()[0]

def get_global_catch_count(character_id):
    c.execute("SELECT COUNT(*) FROM user_catches WHERE character_id=?", (character_id,))
    return c.fetchone()[0]

def get_uploader_info(uploader_id):
    c.execute("SELECT first_name, username FROM users WHERE user_id=?", (uploader_id,))
    row = c.fetchone()
    if row:
        return row[0], row[1]
    return "Unknown", None

def get_leaderboard(limit=10):
    c.execute('''SELECT u.user_id, u.first_name, u.username, COUNT(uc.catch_number) as total_catches
                 FROM users u
                 LEFT JOIN user_catches uc ON u.user_id = uc.user_id
                 GROUP BY u.user_id
                 ORDER BY total_catches DESC
                 LIMIT ?''', (limit,))
    return c.fetchall()

def set_admin_code(code):
    c.execute("REPLACE INTO config (key, value) VALUES ('admin_code', ?)", (code,))
    conn.commit()

def check_admin_code(code):
    c.execute("SELECT value FROM config WHERE key='admin_code'")
    row = c.fetchone()
    return row and row[0] == code

def add_role(user_id, role, granter_id):
    try:
        c.execute("INSERT OR REPLACE INTO user_roles (user_id, role, granted_by, granted_date) VALUES (?, ?, ?, ?)",
                  (user_id, role, granter_id, datetime.now().date()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def remove_role(user_id):
    c.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
    conn.commit()
    return c.rowcount > 0

def list_ranks():
    c.execute("SELECT name, chance, sell_price FROM ranks")
    return c.fetchall()

def add_rank(name, chance, sell_price):
    try:
        c.execute("INSERT INTO ranks (name, chance, sell_price) VALUES (?, ?, ?)", (name, chance, sell_price))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def remove_rank(name):
    c.execute("DELETE FROM ranks WHERE name=?", (name,))
    conn.commit()
    return c.rowcount > 0

# ========== USER COMMANDS ==========
async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    add_user_if_not_exists(user.id, user.first_name, user.username)
    await update.message.reply_text(
        f"🎣 *Welcome to Catcher Bot!*\n\n"
        f"Capture characters and build your *HARM*.\n"
        f"💰 Currency: `NEX`\n\n"
        f"📜 *Commands:*\n"
        f"/catch `<name>` - Try to catch a character\n"
        f"/harm - View your HARM with numbers\n"
        f"/see `<number>` - View details of a character in your HARM\n"
        f"/balance - Check NEX balance\n"
        f"/transfer `@user` `<amount>` - Send NEX\n"
        f"/sell `<number>` - Sell a character\n"
        f"/gift `<number>` `@username` - Gift character\n"
        f"/fav `<number>` - Favorite/unfavorite\n"
        f"/search `<name or anime>` - Search characters\n"
        f"/list - All catchable characters\n"
        f"/stats - Your stats\n"
        f"/leaderboard - Top catchers\n\n"
        f"👑 Admin code: `/claimadmin <code>`",
        parse_mode="Markdown"
    )

async def catch_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if is_timeouted(user_id):
        await update.message.reply_text("⏰ *You are in timeout!* Cannot catch now.", parse_mode="Markdown")
        return

    args = context.args
    if not args:
        await update.message.reply_text("❌ Usage: `/catch <character_name>`", parse_mode="Markdown")
        return

    char_name = " ".join(args)
    char = get_character_by_name(char_name)
    if not char:
        await update.message.reply_text(f"❌ Character `{char_name}` not found.\nUse /list to see available.", parse_mode="Markdown")
        return

    char_id, name, rank, anime, event, media_type, file_id = char
    chance, _ = get_rank_info(rank)
    success = random.randint(1, 100) <= chance

    if user_has_caught(user_id, char_id):
        await update.message.reply_text(f"⚠️ You already caught *{name}*! Each character only once.", parse_mode="Markdown")
        return

    if success:
        catch_number = add_catch(user_id, char_id)
        caption = (
            f"🎉 *SUCCESS!* You caught **{name}**!\n"
            f"✨ Rank: `{rank}` (Chance: {chance}%)\n"
            f"🎬 Anime: `{anime}`\n"
            f"🎁 Event: `{event if event else 'None'}`\n"
            f"🔢 *Harm Number:* `{catch_number}`\n\n"
            f"Use `/see {catch_number}` for details."
        )
        if media_type == "photo":
            await update.message.reply_photo(photo=file_id, caption=caption, parse_mode="Markdown")
        elif media_type == "video":
            await update.message.reply_video(video=file_id, caption=caption, parse_mode="Markdown")
        else:
            await update.message.reply_text(caption, parse_mode="Markdown")
    else:
        await update.message.reply_text(f"😭 *Failed to catch {name}!* (Chance: {chance}%)\nTry again later.", parse_mode="Markdown")

async def see_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ Usage: `/see <harm_number>`\nExample: `/see 3`", parse_mode="Markdown")
        return
    try:
        num = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number.", parse_mode="Markdown")
        return
    
    catch = get_catch_by_number(user_id, num)
    if not catch:
        await update.message.reply_text(f"❌ No character found with number #{num} in your HARM.", parse_mode="Markdown")
        return
    
    char_id, is_fav = catch
    char = get_character_by_id(char_id)
    if not char:
        await update.message.reply_text("❌ Character data missing.")
        return
    
    char_id, name, rank, anime, event, media_type, file_id, added_by = char
    
    global_count = get_global_catch_count(char_id)
    uploader_first, uploader_username = get_uploader_info(added_by)
    uploader_str = uploader_first
    if uploader_username:
        uploader_str += f" (@{uploader_username})"
    
    _, sell_price = get_rank_info(rank)
    
    text = "🔍 *Character Details*\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📛 *Name:* {name}\n"
    text += f"⭐ *Rank:* `{rank}`\n"
    text += f"📺 *Anime:* {anime}\n"
    if event:
        text += f"🎉 *Event:* {event}\n"
    text += f"💰 *Sell price:* `{sell_price} NEX`\n"
    text += f"🔢 *Your Harm #:* `{num}`\n"
    text += f"⭐ *Favorite:* {'Yes' if is_fav else 'No'}\n"
    text += f"📅 *Caught on:* `{datetime.now().date()}`\n"
    text += "\n📊 *Global Stats*\n"
    text += f"👥 *Owned by:* `{global_count}` user(s)\n"
    text += f"👤 *Uploaded by:* {uploader_str}\n"
    
    if media_type == "photo":
        await update.message.reply_photo(photo=file_id, caption=text, parse_mode="Markdown")
    elif media_type == "video":
        await update.message.reply_video(video=file_id, caption=text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def harm_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    catches = get_user_harm_with_numbers(user_id)
    if not catches:
        await update.message.reply_text("📭 *Your HARM is empty.* Use /catch to start capturing!", parse_mode="Markdown")
        return

    text = "🎁 *━━━━━━━━━━━━━━━━━━━━━*\n"
    text += "     ✨ *YOUR HARM* ✨\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n\n"
    for num, name, rank, anime, event, date, fav in catches:
        star = "⭐ " if fav else ""
        price = get_rank_info(rank)[1]
        text += f"🔢 *{star}#{num}*\n"
        text += f"📛 *Name:* {name}\n"
        text += f"⭐ *Rank:* `{rank}`\n"
        text += f"📺 *Anime:* {anime}\n"
        if event:
            text += f"🎉 *Event:* {event}\n"
        text += f"💰 *Sell price:* `{price} NEX`\n"
        text += f"📅 *Caught:* `{date}`\n"
        text += "─────────────────\n"
    text += f"\n💎 *Total:* `{len(catches)}` characters\n"
    text += "━━━━━━━━━━━━━━━━━━━━━"
    await update.message.reply_text(text, parse_mode="Markdown")

async def balance_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    bal = get_nex_balance(user_id)
    await update.message.reply_text(f"💰 *Your NEX balance:* `{bal}`", parse_mode="Markdown")

async def transfer_command(update: Update, context: CallbackContext):
    sender_id = update.effective_user.id
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Usage: `/transfer @username <amount>`", parse_mode="Markdown")
        return
    target = args[0]
    if not target.startswith('@'):
        await update.message.reply_text("❌ Please use @username.", parse_mode="Markdown")
        return
    try:
        amount = int(args[1])
        if amount <= 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Amount must be a positive number.", parse_mode="Markdown")
        return
    username = target[1:]
    c.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    if not row:
        await update.message.reply_text(f"❌ User `{target}` not found.", parse_mode="Markdown")
        return
    receiver_id = row[0]
    if sender_id == receiver_id:
        await update.message.reply_text("❌ You cannot transfer to yourself.", parse_mode="Markdown")
        return
    if get_nex_balance(sender_id) < amount:
        await update.message.reply_text(f"❌ Insufficient NEX. Your balance: `{get_nex_balance(sender_id)}`", parse_mode="Markdown")
        return
    transfer_nex(sender_id, receiver_id, amount)
    await update.message.reply_text(f"✅ You sent `{amount} NEX` to {target}!\nNew balance: `{get_nex_balance(sender_id)}`", parse_mode="Markdown")
    try:
        await context.bot.send_message(receiver_id, f"💰 You received `{amount} NEX` from {update.effective_user.first_name}!", parse_mode="Markdown")
    except:
        pass

async def sell_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ Usage: `/sell <harm_number>`", parse_mode="Markdown")
        return
    try:
        num = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number.", parse_mode="Markdown")
        return
    catch = get_catch_by_number(user_id, num)
    if not catch:
        await update.message.reply_text("❌ Invalid number or you don't own this character.", parse_mode="Markdown")
        return
    char_id = catch[0]
    char = get_character_by_id(char_id)
    if not char:
        await update.message.reply_text("❌ Character not found.")
        return
    rank = char[2]
    _, price = get_rank_info(rank)
    remove_catch_by_number(user_id, num)
    update_nex_balance(user_id, price)
    await update.message.reply_text(f"💰 You sold `{char[1]}` (#{num}) for `{price} NEX`!\nNew balance: `{get_nex_balance(user_id)}`", parse_mode="Markdown")

async def gift_command(update: Update, context: CallbackContext):
    