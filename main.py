import asyncio
import logging
import os
import sqlite3
import random
import threading
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from fastapi import FastAPI, Request
import uvicorn

# Initialize Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ================= 1. DEFINE SYSTEM STATES =================
class BotStates(StatesGroup):
    choosing_category = State()
    writing_confession = State()
    writing_comment = State()

CATEGORIES = ["General 📝", "Love ❤️", "Academic 🎓", "Campus Life 🏫", "Shoutout 🗣️", "Funny 😂"]

# ================= 2. INITIALIZE GLOBAL OBJECTS ONCE =================
bot: Bot = None
dp = Dispatcher(storage=MemoryStorage())  # Crucial: Singleton instantiation

CHANNEL_USERNAME = "@wku_confessions_official"
ADMIN_GROUP_ID = -1003923693636
BOT_USERNAME = "wku_confessionsbot"

# ================= 3. ANTI-CRASH PERSISTENT DATABASE =================
DB_FILE = "confessions.db"
db_lock = threading.Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
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
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS identity_map (
            conf_id INTEGER,
            user_id INTEGER,
            fake_name TEXT,
            PRIMARY KEY (conf_id, user_id)
        )
        """)
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
    conn.execute("PRAGMA journal_mode=WAL;") 
    return conn

# ================= 4. ANONYMOUS IDENTITY ENGINE =================
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

# ================= 5. USER FLOW & INTAKE =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("reply_"):
        try:
            conf_id = int(args[1].split("_")[1])
            await state.update_data(target_conf_id=conf_id)
            await state.set_state(BotStates.writing_comment)
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
@dp.message(BotStates.writing_confession, F.chat.type == "private")
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
        
    if not text and not file_id:
        await message.answer("⚠️ Unrecognized format. Please submit text, standard photo, or a video.")
        return

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO confessions (text, file_id, file_type, category) VALUES (?, ?, ?, ?)",
        (text, file_id, file_type, category)
    )
    conf_id = cursor.lastrowid
    db.commit()
    db.close()
    
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
        logging.info(f"📬 Sent confession #{conf_id} to admin group.")
    except Exception as e:
        logging.error(f"❌ Failed forwarding confession to Admin Group: {e}")

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
        await callback.answer("Confession missing from database.")
        db.close()
        return
        
    text, file_id, file_type, category = row
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
        if callback.message.photo or callback.message.video:
            await callback.message.edit_caption(caption=f"✅ Approved!\nID: #{conf_id}")
        else:
            await callback.message.edit_text(text=f"✅ Approved!\nID: #{conf_id}")
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
        parent_reply_id = state_data.get("parent_reply_msg_id") or disc_msg_id
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

# ================= 10. LIFESPAN MANAGEMENT SYSTEM =================
WEBHOOK_PATH = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot, WEBHOOK_PATH
    
    token = os.getenv("API_TOKEN")
    url = os.getenv("RENDER_URL", "https://wku-confession-bot-8aoc.onrender.com")
    
    if not token:
        logging.critical("❌ DEPLOYMENT CRASH: 'API_TOKEN' missing at instantiation runtime!")
        raise RuntimeError("Missing API_TOKEN")
        
    bot = Bot(token=token)
    WEBHOOK_PATH = f"/webhook/{token[:10]}"
    target_webhook_url = f"{url}/webhook/{token[:10]}"
    
    logging.info("🏁 Connecting webhook pipelines...")
    await bot.set_webhook(
        url=target_webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"]
    )
    logging.info(f"🚀 Live secure gateway locked onto: {target_webhook_url}")
    
    yield
    logging.info("🛑 Severing gateway connections...")
    if bot:
        await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def read_root():
    return {"status": "operational", "engine": "aiogram3"}

@app.post("/webhook/{token_prefix}")
async def process_webhook_payload(token_prefix: str, request: Request):
    try:
        payload = await request.json()
        update = types.Update.model_validate(payload, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.error(f"Webhook tracking execution error: {e}")
    return {"ok": True}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, factory=False)
