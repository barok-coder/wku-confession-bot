import asyncio
import logging
import os
import sqlite3
import random
import threading

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from fastapi import FastAPI, Request
import uvicorn

# ================= 1. SYSTEM CONFIGURATION =================

API_TOKEN = os.getenv("API_TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "https://wku-confession-bot-8aoc.onrender.com")

if not API_TOKEN:
    raise RuntimeError("CRITICAL ERROR: API_TOKEN environment variable is missing!")

CHANNEL_USERNAME = "@wku_confessions_official"
ADMIN_GROUP_ID = -1003923693636

WEBHOOK_PATH = f"/webhook/{API_TOKEN[:10]}"
WEBHOOK_URL = f"{RENDER_URL}{WEBHOOK_PATH}"
BOT_USERNAME = "wku_confessionsbot"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize Engine
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================= 2. ANTI-CRASH PERSISTENT DATABASE =================

DB_FILE = "confessions.db"
db_lock = threading.Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        
        # Confessions Vault
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS confessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            file_id TEXT,
            file_type TEXT,
            category TEXT,
            channel_msg_id INTEGER,
            discussion_chat_id INTEGER,
            discussion_msg_id INTEGER,
            likes INTEGER DEFAULT 0,
            dislikes INTEGER DEFAULT 0
        )
        """)
        
        # Threaded Identity Mapping
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS identity_map (
            conf_id INTEGER,
            user_id INTEGER,
            fake_name TEXT,
            PRIMARY KEY (conf_id, user_id)
        )
        """)
        
        # Nested Comment History
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conf_id INTEGER,
            chat_id INTEGER,
            msg_id INTEGER
        )
        """)
        conn.commit()
        conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")  # Render optimization for fast SQLite reading
    return conn

# ================= 3. ANONYMOUS IDENTITY ENGINE =================

ANIMALS = ["Lion", "Fox", "Cheetah", "Owl", "Eagle", "Wolf", "Hawk", "Panther", "Leopard", "Shark"]
ADJECTIVES = ["WKU_Senior", "Freshman", "Anonymous", "Hidden", "Shadow", "Silent", "Mysterious", "Clever"]

def get_or_create_identity(conf_id: int, user_id: int) -> str:
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT fake_name FROM identity_map WHERE conf_id=? AND user_id=?", (conf_id, user_id))
    row = cursor.fetchone()
    
    if row:
        name = row[0]
    else:
        name = f"{random.choice(ADJECTIVES)} {random.choice(ANIMALS)}"
        try:
            cursor.execute("INSERT INTO identity_map VALUES (?, ?, ?)", (conf_id, user_id, name))
            db.commit()
        except sqlite3.IntegrityError:
            pass
    db.close()
    return name

# ================= 4. FINITE STATE MACHINE (FSM) =================

class BotStates(StatesGroup):
    choosing_category = State()
    writing_confession = State()
    writing_comment = State()

CATEGORIES = ["General 📝", "Love ❤️", "Academic 🎓", "Campus Life 🏫", "Shoutout 🗣️", "Funny 😂"]

# ================= 5. USER FLOW & INTAKE =================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    args = message.text.split()
    
    # Reddit Thread Comment Deep-link Routing
    if len(args) > 1 and args[1].startswith("reply_"):
        try:
            conf_id = int(args[1].split("_")[1])
            await state.update_data(target_conf_id=conf_id)
            await state.set_state(BotStates.writing_comment)
            
            # Generate or fetch masked user name
            identity = get_or_create_identity(conf_id, message.from_user.id)
            await message.answer(f"🎭 Mask active: You are posting as **{identity}**.\n\nWrite your comment or reply below:")
            return
        except Exception:
            await message.answer("⚠️ Broken reply reference link.")
            return

    await state.clear()
    kb = InlineKeyboardBuilder()
    for cat in CATEGORIES:
        kb.button(text=cat, callback_data=f"select_cat:{cat}")
    kb.adjust(2)
    
    await state.set_state(BotStates.choosing_category)
    await message.answer("Welcome to **WKU Confessions**! 🤫\nChoose a category for your submission:", reply_markup=kb.as_markup())

@dp.callback_query(BotStates.choosing_category, F.data.startswith("select_cat:"))
async def process_category(callback: types.CallbackQuery, state: FSMContext):
    selected_cat = callback.data.split(":")[1]
    await state.update_data(chosen_category=selected_cat)
    await state.set_state(BotStates.writing_confession)
    await callback.message.edit_text(f"Selected Category: **{selected_cat}**\n\nNow, type your confession or send your media (Photo / Video):")
    await callback.answer()

# ================= 6. SUBMISSION PROCESSING =================

@dp.message(BotStates.writing_confession, F.chat.type == "private", F.text | F.photo | F.video)
async def handle_submission(message: types.Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("chosen_category", "General 📝")
    
    text = message.text or message.caption or ""
    file_id, file_type = None, None
    
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO confessions (text, file_id, file_type, category) VALUES (?, ?, ?, ?)",
        (text, file_id, file_type, category)
    )
    conf_id = cursor.lastrowid
    db.commit()
    db.close()
    
    # Render Admin Verification Card
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Approve", callback_data=f"adm_approve:{conf_id}")
    kb.button(text="❌ Reject", callback_data=f"adm_reject:{conf_id}")
    
    admin_caption = f"🏷️ Category: **{category}**\n🆔 Queue ID: `#{conf_id}`\n\n📝 **Confession:**\n{text}"
    
    try:
        if file_type == "photo":
            await bot.send_photo(ADMIN_GROUP_ID, file_id, caption=admin_caption, reply_markup=kb.as_markup())
        elif file_type == "video":
            await bot.send_video(ADMIN_GROUP_ID, file_id, caption=admin_caption, reply_markup=kb.as_markup())
        else:
            await bot.send_message(ADMIN_GROUP_ID, text=admin_caption, reply_markup=kb.as_markup())
    except Exception as e:
        logging.error(f"Admin forward failed: {e}")

    await message.answer("📥 Submitted anonymously! It is currently in the admin moderation review queue.")
    await state.clear()

# ================= 7. ADM MODERATION & REACTIONS =================

@dp.callback_query(F.data.startswith("adm_approve:"))
async def approve_confession(callback: types.CallbackQuery):
    conf_id = int(callback.data.split(":")[1])
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT text, file_id, file_type, category FROM confessions WHERE id=?", (conf_id,))
    row = cursor.fetchone()
    
    if not row:
        await callback.answer("Confession payload missing.")
        db.close()
        return
        
    text, file_id, file_type, category = row
    
    # Render Public Layout & Sub-menus
    public_text = f"📢 **WKU Confession #{conf_id}**\n🏷️ Category: {category}\n\n{text}"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Add Comment", url=f"https://t.me/{BOT_USERNAME}?start=reply_{conf_id}")
    kb.button(text="👍 0", callback_data=f"react:like:{conf_id}")
    kb.button(text="👎 0", callback_data=f"react:dislike:{conf_id}")
    kb.adjust(1, 2)
    
    if file_type == "photo":
        out = await bot.send_photo(CHANNEL_USERNAME, file_id, caption=public_text, reply_markup=kb.as_markup())
    elif file_type == "video":
        out = await bot.send_video(CHANNEL_USERNAME, file_id, caption=public_text, reply_markup=kb.as_markup())
    else:
        out = await bot.send_message(CHANNEL_USERNAME, text=public_text, reply_markup=kb.as_markup())
        
    cursor.execute("UPDATE confessions SET channel_msg_id=? WHERE id=?", (out.message_id, conf_id))
    db.commit()
    db.close()
    
    try:
        await callback.message.edit_caption(caption=f"✅ Approved via pipeline!\nID: #{conf_id}") if callback.message.photo or callback.message.video else await callback.message.edit_text(text=f"✅ Approved!\nID: #{conf_id}")
    except Exception:
        pass
    await callback.answer("Broadcast complete.")

@dp.callback_query(F.data.startswith("adm_reject:"))
async def reject_confession(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Purged from pipeline queue.")

@dp.callback_query(F.data.startswith("react:"))
async def handle_reactions(callback: types.CallbackQuery):
    _, r_type, conf_id = callback.data.split(":")
    conf_id = int(conf_id)
    
    db = get_db()
    cursor = db.cursor()
    
    if r_type == "like":
        cursor.execute("UPDATE confessions SET likes = likes + 1 WHERE id=?", (conf_id,))
    else:
        cursor.execute("UPDATE confessions SET dislikes = dislikes + 1 WHERE id=?", (conf_id,))
    db.commit()
    
    cursor.execute("SELECT likes, dislikes FROM confessions WHERE id=?", (conf_id,))
    likes, dislikes = cursor.fetchone()
    db.close()
    
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Add Comment", url=f"https://t.me/{BOT_USERNAME}?start=reply_{conf_id}")
    kb.button(text=f"👍 {likes}", callback_data=f"react:like:{conf_id}")
    kb.button(text=f"👎 {dislikes}", callback_data=f"react:dislike:{conf_id}")
    kb.adjust(1, 2)
    
    try:
        await callback.message.edit_reply_markup(reply_markup=kb.as_markup())
        await callback.answer("Reaction updated!")
    except Exception:
        await callback.answer("Processing error.")

# ================= 8. SYSTEM SYNC & THREADED DISCUSSIONS =================

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def catch_discussion_mirror(message: types.Message):
    """Hooks into automatic channel cloning event inside your public discussion chat"""
    try:
        if message.forward_from_chat and message.forward_from_chat.username == CHANNEL_USERNAME.replace("@", ""):
            orig_msg_id = message.forward_from_message_id
            
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "UPDATE confessions SET discussion_chat_id=?, discussion_msg_id=? WHERE channel_msg_id=?",
                (message.chat.id, message.message_id, orig_msg_id)
            )
            db.commit()
            db.close()
            logging.info(f"🎯 SYSTEM SYNC: Linked Channel Post #{orig_msg_id} to Group Chat Thread {message.message_id}")
    except Exception as e:
        logging.error(f"Sync intercept error: {e}")

# ================= 9. REDDIT-STYLE NESTED THREAD COMMENTS =================

@dp.message(BotStates.writing_comment, F.text)
async def process_threaded_comment(message: types.Message, state: FSMContext):
    state_data = await state.get_data()
    conf_id = state_data.get("target_conf_id")
    
    # Multi-second asynchronous loop buffer to ensure synchronization
    row = None
    for _ in range(4):
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT discussion_chat_id, discussion_msg_id FROM confessions WHERE id=?", (conf_id,))
        row = cursor.fetchone()
        db.close()
        if row and row[0] and row[1]:
            break
        await asyncio.sleep(1)
        
    if not row or not row[0] or not row[1]:
        await message.answer("⚠️ Thread synchronization processing active. Try again in 5 seconds.")
        await state.clear()
        return
        
    disc_chat_id, disc_msg_id = row[0], row[1]
    identity = get_or_create_identity(conf_id, message.from_user.id)
    
    try:
        # Check if the user is replying to a specific comment tree context inside the bot
        parent_reply_id = state_data.get("parent_reply_msg_id") or disc_msg_id
        
        # Route directly inside the native comment drawer
        sent = await bot.send_message(
            chat_id=disc_chat_id,
            text=f"💬 **{identity}**:\n\n{message.text}",
            reply_to_message_id=parent_reply_id
        )
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute("INSERT INTO comments (conf_id, chat_id, msg_id) VALUES (?, ?, ?)", (conf_id, disc_chat_id, sent.message_id))
        db.commit()
        db.close()
        
        await message.answer("🚀 Your anonymous reply has been woven into the post's comment thread!")
    except Exception as e:
        logging.error(f"Nested thread posting crash: {e}")
        await message.answer("❌ Error routing thread reply to Telegram.")
        
    await state.clear()

# ================= 10. FASTAPI WEBHOOK PIPELINE =================

app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "operational", "mode": "webhook_active", "engine": "aiogram3"}

@app.post(WEBHOOK_PATH)
async def process_webhook_payload(request: Request):
    try:
        payload = await request.json()
        update = types.Update.model_validate(payload, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.error(f"Webhook execution failure: {e}")
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    logging.info("Initializing Render container boots...")
    await bot.set_webhook(
        url=WEBHOOK_URL,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"]
    )
    logging.info(f"🚀 Webhook pipeline locked and loaded onto target URL: {WEBHOOK_URL}")

@app.on_event("shutdown")
async def on_shutdown():
    logging.info("Closing down environment tunnels...")
    await bot.session.close()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
