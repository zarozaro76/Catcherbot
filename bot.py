import logging
import sqlite3
import random
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

TOKEN = "8346074021:AAHmYoCI-PUo4xUYoJMUSUOKgzl6Ku3aOvI"
OWNER_ID = 8579186775

user_command_count = defaultdict(list)
user_mute_until = {}

def is_muted(user_id):
    if user_id == OWNER_ID:
        return False
    if get_user_role(user_id) in ('admin', 'owner'):
        return False
    if user_id in user_mute_until and datetime.now() < user_mute_until[user_id]:
        return True
    return False

def check_spam(user_id):
    if user_id == OWNER_ID or get_user_role(user_id) in ('admin', 'owner'):
        return True
    now = datetime.now()
    user_command_count[user_id] = [t for t in user_command_count[user_id] if now - t < timedelta(seconds=3)]
    if len(user_command_count[user_id]) >= 10:
        user_mute_until[user_id] = now + timedelta(minutes=10)
        user_command_count[user_id] = []
        return False
    user_command_count[user_id].append(now)
    return True

message_counter = 0
AUTO_SPAWN_THRESHOLD = 70

async def auto_spawn(context: CallbackContext, chat_id: int):
    char = get_random_character()
    if not char:
        return
    char_id, name, rank, anime, event, media_type, file_id = char
    add_global_spawn(char_id)
    caption = (
        f"🎲 *A wild {name} has appeared!*\n"
        f"✨ Rank: `{rank}`\n"
        f"🎬 Anime: `{anime}`\n"
        f"🎁 Event: `{event if event else 'None'}`\n\n"
        f"Use `/catch` to try to catch it!"
    )
    try:
        if media_type == "photo":
            await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption, parse_mode="Markdown")
        elif media_type == "video":
            await context.bot.send_video(chat_id=chat_id, video=file_id, caption=caption, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode="Markdown")
    except Exception as e:
        print(f"Auto spawn failed: {e}")

conn = sqlite3.connect('catcher.db', check_same_thread=False)
c = conn.cursor()

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

c.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    joined DATE,
    nex_balance INTEGER DEFAULT 0
)''')

c.execute('''CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER PRIMARY KEY,
    role TEXT CHECK(role IN ('admin', 'uploader')),
    granted_by INTEGER,
    granted_date DATE
)''')

c.execute('''CREATE TABLE IF NOT EXISTS user_catches (
    user_id INTEGER,
    catch_number INTEGER,
    character_id INTEGER,
    caught_date DATE,
    is_favorite INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, catch_number),
    FOREIGN KEY(character_id) REFERENCES characters(id)
)''')

c.execute('''CREATE TABLE IF NOT EXISTS global_spawns (
    spawn_id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER,
    spawned_at DATETIME,
    caught_by INTEGER DEFAULT NULL,
    FOREIGN KEY(character_id) REFERENCES characters(id)
)''')

c.execute('''CREATE TABLE IF NOT EXISTS ranks (
    name TEXT PRIMARY KEY,
    chance INTEGER DEFAULT 50,
    sell_price INTEGER DEFAULT 10
)''')

default_ranks = [('Epic', 25, 200), ('Legendary', 10, 500), ('Mythic', 5, 1200), 
                 ('Exotic', 3, 3000), ('Op', 1, 8000), ('Universe', 0.5, 20000)]
for name, chance, price in default_ranks:
    c.execute("INSERT OR IGNORE INTO ranks (name, chance, sell_price) VALUES (?, ?, ?)", (name, chance, price))

c.execute('''CREATE TABLE IF NOT EXISTS user_timeouts (
    user_id INTEGER PRIMARY KEY,
    until DATETIME
)''')

c.execute('''CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
)''')

conn.commit()

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

def get_random_character():
    c.execute("SELECT id, name, rank, anime, event, media_type, media_file_id FROM characters ORDER BY RANDOM() LIMIT 1")
    return c.fetchone()

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
    return new_val == 1

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

def add_global_spawn(character_id):
    c.execute("INSERT INTO global_spawns (character_id, spawned_at) VALUES (?, ?)", (character_id, datetime.now()))
    conn.commit()
    return c.lastrowid

def get_active_spawn():
    c.execute("SELECT spawn_id, character_id FROM global_spawns WHERE caught_by IS NULL ORDER BY spawned_at DESC LIMIT 1")
    row = c.fetchone()
    if row:
        return row[0], row[1]
    return None, None

def claim_spawn(spawn_id, user_id):
    c.execute("UPDATE global_spawns SET caught_by = ? WHERE spawn_id = ? AND caught_by IS NULL", (user_id, spawn_id))
    conn.commit()
    return c.rowcount > 0

async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    add_user_if_not_exists(user.id, user.first_name, user.username)
    await update.message.reply_text(
        f"🎣 *Welcome to Catcher Bot!*\n\n"
        f"Capture characters and build your *HARM*.\n"
        f"💰 Currency: `NEX`\n\n"
        f"📜 *Commands:*\n"
        f"/catch `<name>` - Catch a character by name\n"
        f"/catch - Catch the currently spawned character\n"
        f"/harm - View your HARM\n"
        f"/see `<number>` - View details of a character in your HARM\n"
        f"/view `<character_id>` - View any character by ID\n"
        f"/balance - Check NEX balance\n"
        f"/transfer `@user` `<amount>` - Send NEX\n"
        f"/sell `<number>` - Sell a character\n"
        f"/gift `<number>` `@username` - Gift a character\n"
        f"/fav `<number>` - Favorite/unfavorite\n"
        f"/search `<name or anime>` - Search characters\n"
        f"/list - All catchable characters\n"
        f"/stats - Your stats\n"
        f"/leaderboard - Top catchers\n\n"
        f"👑 Admin: `/claimadmin <code>`\n"
        f"🔄 Auto-spawn every {AUTO_SPAWN_THRESHOLD} messages!\n"
        f"⛔ Anti-spam: 10 commands in 3 seconds = 10 min mute",
        parse_mode="Markdown"
    )

async def catch_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if is_timeouted(user_id):
        await update.message.reply_text("⏰ *You are in timeout!* Cannot catch now.", parse_mode="Markdown")
        return
    if is_muted(user_id):
        return

    args = context.args
    if not args:
        spawn_id, char_id = get_active_spawn()
        if not spawn_id:
            await update.message.reply_text("❌ No character is currently spawned. Wait for auto-spawn or admin to spawn one using `/spawn`.", parse_mode="Markdown")
            return
        char = get_character_by_id(char_id)
        if not char:
            await update.message.reply_text("❌ Spawned character not found.", parse_mode="Markdown")
            return
        char_id, name, rank, anime, event, media_type, file_id = char
        chance, _ = get_rank_info(rank)
        success = random.randint(1, 100) <= chance

        if user_has_caught(user_id, char_id):
            await update.message.reply_text(f"⚠️ You already caught *{name}*! Each character only once.", parse_mode="Markdown")
            return

        if success:
            if claim_spawn(spawn_id, user_id):
                catch_number = add_catch(user_id, char_id)
                caption = (f"🎉 *SUCCESS!* You caught **{name}** from the spawned pool!\n"
                          f"✨ Rank: `{rank}` (Chance: {chance}%)\n"
                          f"🎬 Anime: `{anime}`\n"
                          f"🎁 Event: `{event if event else 'None'}`\n"
                          f"🔢 *Harm Number:* `{catch_number}`")
                if media_type == "photo":
                    await update.message.reply_photo(photo=file_id, caption=caption, parse_mode="Markdown")
                elif media_type == "video":
                    await update.message.reply_video(video=file_id, caption=caption, parse_mode="Markdown")
                else:
                    await update.message.reply_text(caption, parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ This character was already caught by someone else!", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"😭 *Failed to catch {name}!* (Chance: {chance}%)\nTry again later.", parse_mode="Markdown")
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
        caption = (f"🎉 *SUCCESS!* You caught **{name}**!\n"
                  f"✨ Rank: `{rank}` (Chance: {chance}%)\n"
                  f"🎬 Anime: `{anime}`\n"
                  f"🎁 Event: `{event if event else 'None'}`\n"
                  f"🔢 *Harm Number:* `{catch_number}`")
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
    
    text = "🔍 *Character Details*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📛 *Name:* {name}\n⭐ *Rank:* `{rank}`\n📺 *Anime:* {anime}\n"
    if event:
        text += f"🎉 *Event:* {event}\n"
    text += f"💰 *Sell price:* `{sell_price} NEX`\n🔢 *Your Harm #:* `{num}`\n"
    text += f"⭐ *Favorite:* {'Yes' if is_fav else 'No'}\n📅 *Caught on:* `{datetime.now().date()}`\n"
    text += "\n📊 *Global Stats*\n👥 *Owned by:* `{global_count}` user(s)\n👤 *Uploaded by:* {uploader_str}\n"
    
    if media_type == "photo":
        await update.message.reply_photo(photo=file_id, caption=text, parse_mode="Markdown")
    elif media_type == "video":
        await update.message.reply_video(video=file_id, caption=text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def view_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ Usage: `/view <character_id>`\nExample: `/view 42`", parse_mode="Markdown")
        return
    try:
        char_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Character ID must be a number.", parse_mode="Markdown")
        return

    char = get_character_by_id(char_id)
    if not char:
        await update.message.reply_text(f"❌ No character found with ID `{char_id}`.", parse_mode="Markdown")
        return

    char_id, name, rank, anime, event, media_type, file_id, added_by = char
    global_count = get_global_catch_count(char_id)
    uploader_first, uploader_username = get_uploader_info(added_by)
    uploader_str = uploader_first
    if uploader_username:
        uploader_str += f" (@{uploader_username})"
    _, sell_price = get_rank_info(rank)
    user_owns = user_has_caught(user_id, char_id)

    text = "🔍 *Character Details*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"🆔 *ID:* `{char_id}`\n📛 *Name:* {name}\n⭐ *Rank:* `{rank}`\n📺 *Anime:* {anime}\n"
    if event:
        text += f"🎉 *Event:* {event}\n"
    text += f"💰 *Sell price:* `{sell_price} NEX`\n\n📊 *Global Stats*\n"
    text += f"👥 *Owned by:* `{global_count}` user(s)\n👤 *Uploaded by:* {uploader_str}\n"
    if user_owns:
        text += "\n✅ *You own this character.*"
    else:
        text += "\n❌ *You do not own this character.*"

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

    text = "🎁 *━━━━━━━━━━━━━━━━━━━━━*\n     ✨ *YOUR HARM* ✨\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for num, name, rank, anime, event, date, fav in catches:
        star = "⭐ " if fav else ""
        price = get_rank_info(rank)[1]
        text += f"🔢 *{star}#{num}*\n📛 *Name:* {name}\n⭐ *Rank:* `{rank}`\n📺 *Anime:* {anime}\n"
        if event:
            text += f"🎉 *Event:* {event}\n"
        text += f"💰 *Sell price:* `{price} NEX`\n📅 *Caught:* `{date}`\n─────────────────\n"
    text += f"\n💎 *Total:* `{len(catches)}` characters\n━━━━━━━━━━━━━━━━━━━━━"
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
    sender_id = update.effective_user.id
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Usage: `/gift <harm_number> @username`\nExample: `/gift 2 @friend`", parse_mode="Markdown")
        return
    try:
        num = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ First argument must be a number.", parse_mode="Markdown")
        return
    target = args[1]
    if not target.startswith('@'):
        await update.message.reply_text("❌ Please use @username.", parse_mode="Markdown")
        return
    username = target[1:]
    c.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    if not row:
        await update.message.reply_text(f"❌ User `{target}` not found.", parse_mode="Markdown")
        return
    receiver_id = row[0]
    if sender_id == receiver_id:
        await update.message.reply_text("❌ You cannot gift to yourself.", parse_mode="Markdown")
        return
    catch = get_catch_by_number(sender_id, num)
    if not catch:
        await update.message.reply_text("❌ Invalid number or you don't own this character.", parse_mode="Markdown")
        return
    char_id = catch[0]
    char = get_character_by_id(char_id)
    if not char:
        await update.message.reply_text("❌ Character error.")
        return
    if transfer_character_by_number(sender_id, receiver_id, num):
        await update.message.reply_text(f"🎁 You gifted `{char[1]}` (#{num}) to {target}!", parse_mode="Markdown")
        try:
            await context.bot.send_message(receiver_id, f"🎁 You received `{char[1]}` as a gift from {update.effective_user.first_name}!\nUse /see to view it.", parse_mode="Markdown")
        except:
            pass
    else:
        await update.message.reply_text("❌ Transfer failed.", parse_mode="Markdown")

async def fav_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ Usage: `/fav <harm_number>`", parse_mode="Markdown")
        return
    try:
        num = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number.", parse_mode="Markdown")
        return
    if toggle_favorite(user_id, num):
        await update.message.reply_text(f"⭐ Character #{num} is now a favorite!", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Failed. Make sure #{num} exists.", parse_mode="Markdown")

async def search_command(update: Update, context: CallbackContext):
    args = context.args
    if not args:
        await update.message.reply_text("❌ Usage: `/search <character_name or anime>`", parse_mode="Markdown")
        return
    query = " ".join(args)
    results = search_characters(query)
    if not results:
        await update.message.reply_text(f"🔍 No characters found for `{query}`.", parse_mode="Markdown")
        return
    
    context.user_data['search_results'] = results
    context.user_data['search_query'] = query
    await send_search_page(update, context, page=0)

async def send_search_page(update: Update, context: CallbackContext, page: int):
    results = context.user_data.get('search_results', [])
    if not results:
        return
    items_per_page = 10
    total_pages = (len(results) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    page_results = results[start:end]
    
    text = f"🔍 *Search Results for:* `{context.user_data['search_query']}`\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for idx, (char_id, name, rank, anime, event) in enumerate(page_results, start=start+1):
        text += f"*{idx}. {name}* — `{rank}` — 🆔 `{char_id}`\n   📺 {anime}"
        if event:
            text += f" | 🎉 {event}"
        text += "\n"
    text += f"\n📊 *Results:* `{len(results)}` (Page {page+1}/{total_pages})"
    
    keyboard = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Previous", callback_data=f"search_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"search_page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        await update.callback_query.answer()
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def search_page_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data
    if data.startswith("search_page_"):
        page = int(data.split("_")[2])
        await send_search_page(update, context, page)

async def list_command(update: Update, context: CallbackContext):
    chars = get_all_characters()
    if not chars:
        await update.message.reply_text("📭 No characters available yet. Uploaders can add with /upload.", parse_mode="Markdown")
        return
    text = "📜 *Catchable Characters:*\n\n"
    for char_id, name, rank, anime in chars:
        chance, _ = get_rank_info(rank)
        text += f"🔹 *{name}* | `{rank}` | {chance}% catch rate | 📺 {anime} | 🆔 `{char_id}`\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def stats_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    total = get_total_catches(user_id)
    bal = get_nex_balance(user_id)
    await update.message.reply_text(f"📊 *Your stats:*\n🎣 Characters caught: `{total}`\n💰 NEX: `{bal}`", parse_mode="Markdown")

async def leaderboard_command(update: Update, context: CallbackContext):
    leaderboard = get_leaderboard(10)
    if not leaderboard:
        await update.message.reply_text("📊 No users yet.", parse_mode="Markdown")
        return
    text = "🏆 *LEADERBOARD - Top Catchers* 🏆\n\n"
    for idx, (uid, first_name, username, total_catches) in enumerate(leaderboard, 1):
        name = first_name if first_name else str(uid)
        if username:
            name += f" (@{username})"
        text += f"*{idx}.* {name} — 🎣 `{total_catches}` catches\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def upload_command(update: Update, context: CallbackContext):
    if not is_uploader(update.effective_user.id):
        await update.message.reply_text("⛔ You need 'Uploader' or higher role to upload characters.", parse_mode="Markdown")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Please reply to a photo or video with this command.")
        return
    media_msg = update.message.reply_to_message
    media_type = None
    file_id = None
    if media_msg.photo:
        media_type = "photo"
        file_id = media_msg.photo[-1].file_id
    elif media_msg.video:
        media_type = "video"
        file_id = media_msg.video.file_id
    else:
        await update.message.reply_text("❌ Reply to a **photo** or **video** only.")
        return

    args = context.args
    if len(args) < 4:
        await update.message.reply_text(
            "📖 *Usage:* `/upload [character name] & [anime name] & [rank] & [event name]`\n"
            "• Rank: `Epic`, `Legendary`, `Mythic`, `Exotic`, `Op`, `Universe`\n"
            "• Event optional (use `None` to skip)\n"
            "• *Reply to a photo or video*\n\n"
            "Example: `/upload Goku & DragonBall & Legendary & Tournament`",
            parse_mode="Markdown"
        )
        return

    full_text = " ".join(args)
    parts = [p.strip() for p in full_text.split('&')]
    if len(parts) < 3:
        await update.message.reply_text("❌ Invalid format. Use `Name & Anime & Rank & Event`", parse_mode="Markdown")
        return
    name = parts[0]
    anime = parts[1]
    rank = parts[2]
    event = parts[3] if len(parts) > 3 else None
    if event and event.lower() == 'none':
        event = None

    valid_ranks = ['Epic', 'Legendary', 'Mythic', 'Exotic', 'Op', 'Universe']
    if rank not in valid_ranks:
        await update.message.reply_text(f"❌ Invalid rank. Choose: {', '.join(valid_ranks)}", parse_mode="Markdown")
        return

    success = add_character(name, rank, anime, event, media_type, file_id, update.effective_user.id)
    if success:
        await update.message.reply_text(f"✅ Character `{name}` ({rank}) from `{anime}` added!", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Character `{name}` already exists.", parse_mode="Markdown")

async def claim_admin(update: Update, context: CallbackContext):
    args = context.args
    if not args:
        await update.message.reply_text("❌ Usage: `/claimadmin <code>`", parse_mode="Markdown")
        return
    code = args[0]
    if check_admin_code(code):
        user_id = update.effective_user.id
        if add_role(user_id, 'admin', OWNER_ID):
            await update.message.reply_text("✅ You are now an admin! Full access granted.")
        else:
            await update.message.reply_text("⚠️ You are already an admin.")
    else:
        await update.message.reply_text("❌ Invalid admin code.")

async def set_admin_code_command(update: Update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/setadmincode <new_code>`", parse_mode="Markdown")
        return
    code = args[0]
    set_admin_code(code)
    await update.message.reply_text(f"🔐 Admin code set to `{code}`. Users can /claimadmin.", parse_mode="Markdown")

async def set_role_command(update: Update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/setrole <@username or user_id> <admin|uploader|none>`", parse_mode="Markdown")
        return
    target = args[0]
    role = args[1].lower()
    if role not in ['admin', 'uploader', 'none']:
        await update.message.reply_text("Role must be admin, uploader, or none.", parse_mode="Markdown")
        return

    user_id = None
    if target.startswith('@'):
        username = target[1:]
        c.execute("SELECT user_id FROM users WHERE username = ?", (username,))
        row = c.fetchone()
        if row:
            user_id = row[0]
        else:
            try:
                chat = await context.bot.get_chat(target)
                user_id = chat.id
                add_user_if_not_exists(user_id, chat.first_name, chat.username)
            except Exception:
                await update.message.reply_text(f"User `{target}` not found. Make sure they have started the bot or use numeric ID.", parse_mode="Markdown")
                return
    else:
        try:
            user_id = int(target)
            c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            if not c.fetchone():
                try:
                    chat = await context.bot.get_chat(user_id)
                    first_name = chat.first_name
                    username = chat.username
                except Exception:
                    first_name = str(user_id)
                    username = None
                add_user_if_not_exists(user_id, first_name, username)
        except ValueError:
            await update.message.reply_text("Invalid username or ID.", parse_mode="Markdown")
            return

    if user_id is None:
        await update.message.reply_text("Could not identify user.", parse_mode="Markdown")
        return

    if role == 'none':
        remove_role(user_id)
        await update.message.reply_text(f"Removed special role from `{target}`.", parse_mode="Markdown")
    else:
        add_role(user_id, role, OWNER_ID)
        await update.message.reply_text(f"✅ `{target}` is now `{role}`.", parse_mode="Markdown")

async def remove_char(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/removechar <character_name>`", parse_mode="Markdown")
        return
    name = " ".join(args)
    if remove_character(name):
        await update.message.reply_text(f"🗑️ Character `{name}` removed.")
    else:
        await update.message.reply_text(f"❌ Character `{name}` not found.")

async def give_char_admin(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/givechar <user_id> <character_name>`", parse_mode="Markdown")
        return
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_text("User ID must be a number.")
        return
    char_name = " ".join(context.args[1:])
    char = get_character_by_name(char_name)
    if not char:
        await update.message.reply_text(f"Character `{char_name}` not found.")
        return
    char_id = char[0]
    if user_has_caught(user_id, char_id):
        await update.message.reply_text("User already owns this character.")
        return
    num = add_catch(user_id, char_id)
    await update.message.reply_text(f"🎁 Admin gave `{char_name}` to user {user_id}. Harm number: `{num}`", parse_mode="Markdown")

async def spawn_command(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Only admins and owner can spawn characters.", parse_mode="Markdown")
        return
    
    args = context.args
    if len(args) == 0:
        char = get_random_character()
        if not char:
            await update.message.reply_text("❌ No characters available to spawn.", parse_mode="Markdown")
            return
        char_id, name, rank, anime, event, media_type, file_id = char
        add_global_spawn(char_id)
        caption = (f"🎲 *A wild {name} has appeared!*\n✨ Rank: `{rank}`\n🎬 Anime: `{anime}`\n"
                  f"🎁 Event: `{event if event else 'None'}`\n\nUse `/catch` to try to catch it!")
        if media_type == "photo":
            await update.message.reply_photo(photo=file_id, caption=caption, parse_mode="Markdown")
        elif media_type == "video":
            await update.message.reply_video(video=file_id, caption=caption, parse_mode="Markdown")
        else:
            await update.message.reply_text(caption, parse_mode="Markdown")
        return
    
    try:
        char_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Usage: `/spawn <character_id>` or just `/spawn` for random.", parse_mode="Markdown")
        return
    
    char = get_character_by_id(char_id)
    if not char:
        await update.message.reply_text(f"❌ Character with ID `{char_id}` not found.", parse_mode="Markdown")
        return
    
    char_id, name, rank, anime, event, media_type, file_id = char
    add_global_spawn(char_id)
    caption = (f"🎲 *A wild {name} has been spawned!*\n✨ Rank: `{rank}`\n🎬 Anime: `{anime}`\n"
              f"🎁 Event: `{event if event else 'None'}`\n\nUse `/catch` to try to catch it!")
    if media_type == "photo":
        await update.message.reply_photo(photo=file_id, caption=caption, parse_mode="Markdown")
    elif media_type == "video":
        await update.message.reply_video(video=file_id, caption=caption, parse_mode="Markdown")
    else:
        await update.message.reply_text(caption, parse_mode="Markdown")

async def timeout_user(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Only admins and owner can timeout users.", parse_mode="Markdown")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/timeout <user_id> <minutes>`", parse_mode="Markdown")
        return
    try:
        user_id = int(context.args[0])
        minutes = int(context.args[1])
    except:
        await update.message.reply_text("User ID and minutes must be numbers.")
        return
    set_timeout(user_id, minutes)
    await update.message.reply_text(f"⏳ User {user_id} timed out for {minutes} minutes.")

async def untimeout_user(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Only admins and owner can remove timeout.", parse_mode="Markdown")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: `/untimeout <user_id>`", parse_mode="Markdown")
        return
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_text("User ID must be number.")
        return
    remove_timeout(user_id)
    await update.message.reply_text(f"✅ Timeout removed for user {user_id}.")

async def admin_stats(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM characters")
    total_chars = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM user_catches")
    total_catches = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM user_roles")
    total_roles = c.fetchone()[0]
    await update.message.reply_text(
        f"📊 *Bot Stats*\n👥 Users: `{total_users}`\n🎭 Characters: `{total_chars}`\n"
        f"🎣 Total catches: `{total_catches}`\n👑 Admins/Uploaders: `{total_roles}`",
        parse_mode="Markdown"
    )

async def broadcast(update: Update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: `/broadcast <message>`", parse_mode="Markdown")
        return
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    success = 0
    for (uid,) in users:
        try:
            await context.bot.send_message(uid, f"📢 *Broadcast:*\n{message}", parse_mode="Markdown")
            success += 1
        except:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to {success} users.")

async def add_rank_command(update: Update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: `/addrank <name> <catch_chance%> <sell_price>`\nExample: `/addrank Divine 2 10000`", parse_mode="Markdown")
        return
    name = context.args[0]
    try:
        chance = int(context.args[1])
        price = int(context.args[2])
    except:
        await update.message.reply_text("Chance and price must be numbers.")
        return
    if add_rank(name, chance, price):
        await update.message.reply_text(f"✅ Rank `{name}` added (Chance: {chance}%, Sell price: {price} NEX).", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Rank `{name}` already exists.")

async def remove_rank_command(update: Update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/removerank <name>`", parse_mode="Markdown")
        return
    name = context.args[0]
    if remove_rank(name):
        await update.message.reply_text(f"🗑️ Rank `{name}` removed.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Rank `{name}` not found.")

async def list_ranks_command(update: Update, context: CallbackContext):
    ranks = list_ranks()
    if not ranks:
        await update.message.reply_text("No ranks defined.")
        return
    text = "📊 *Rank settings:*\n\n"
    for name, chance, price in ranks:
        text += f"🔸 *{name}* - Catch chance: `{chance}%` | Sell price: `{price} NEX`\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def message_handler(update: Update, context: CallbackContext):
    global message_counter
    if update.message and update.message.text and not update.message.text.startswith('/'):
        message_counter += 1
        if message_counter >= AUTO_SPAWN_THRESHOLD:
            message_counter = 0
            chat_id = update.effective_chat.id
            await auto_spawn(context, chat_id)

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("catch", catch_command))
    app.add_handler(CommandHandler("see", see_command))
    app.add_handler(CommandHandler("view", view_command))
    app.add_handler(CommandHandler("harm", harm_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("transfer", transfer_command))
    app.add_handler(CommandHandler("sell", sell_command))
    app.add_handler(CommandHandler("gift", gift_command))
    app.add_handler(CommandHandler("fav", fav_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("upload", upload_command))
    app.add_handler(CommandHandler("claimadmin", claim_admin))
    app.add_handler(CommandHandler("setadmincode", set_admin_code_command))
    app.add_handler(CommandHandler("setrole", set_role_command))
    app.add_handler(CommandHandler("removechar", remove_char))
    app.add_handler(CommandHandler("givechar", give_char_admin))
    app.add_handler(CommandHandler("spawn", spawn_command))
    app.add_handler(CommandHandler("timeout", timeout_user))
    app.add_handler(CommandHandler("untimeout", untimeout_user))
    app.add_handler(CommandHandler("adminstats", admin_stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("addrank", add_rank_command))
    app.add_handler(CommandHandler("removerank", remove_rank_command))
    app.add_handler(CommandHandler("listranks", list_ranks_command))
    app.add_handler(CallbackQueryHandler(search_page_callback, pattern="^search_page_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("🤖 Catcher bot is running with:")
    print(f"  - Auto-spawn every {AUTO_SPAWN_THRESHOLD} messages")
    print("  - Anti-spam: 10 commands in 3 seconds = 10 min mute")
    print("  - Admin timeout commands")
    print("  - Spawn by ID or random")
    app.run_polling()

if __name__ == "__main__":
    main()