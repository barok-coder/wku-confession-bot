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
from aiogram.types import ReplyParameters

from fastapi import FastAPI, Request
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ================= 1. STATES =================
class BotStates(StatesGroup):
    choosing_category = State()
    writing_confession = State()
    writing_comment = State()

# Categories mapped to match the screenshot tags
CATEGORIES = [
    "Relationship ",
    "School & Exam ",
    "Mental Health ",
    "General ",
    "Funny "
]

def category_to_hashtags(category: str) -> str:
    cat_lower = category.lower()
    if "relationship" in cat_lower:
        return "#Relationship #Sexual #Mental"
    elif "school" in cat_lower or "exam" in cat_lower:
        return "#School #Exam"
    elif "mental" in cat_lower:
        return "#Mental #Harassment"
    elif "funny" in cat_lower:
        return "#Funny #Humor"
    else:
        return "#General"

# ================= 2. GLOBALS & CONFIGURATION =================
bot: Bot = None
dp = Dispatcher(storage=MemoryStorage())

# Dynamic destination target for the channel (Supports username or numeric ID for private channels)
CHANNEL_ID_ENV = os.getenv("CHANNEL_ID", "@wku_confession")
try:
    CHANNEL_TARGET = int(CHANNEL_ID_ENV)
except ValueError:
    CHANNEL_TARGET = CHANNEL_ID_ENV

CHANNEL_PUBLIC_NAME = "wku_confession"
CHANNEL_USERNAME = f"@{CHANNEL_PUBLIC_NAME}"
BOT_USERNAME = "wku_confessionbot"

# ================= 3. DATABASE =================
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

# ================= 4. IDENTITY ENGINE =================
ANIMALS = ["Lion", "Fox", "Cheetah", "Owl", "Eagle", "Wolf", "Hawk", "Panther", "Leopard", "Shark"]
ADJECTIVES = ["WKU_Senior", "Freshman", "Anonymous", "Hidden", "Shadow", "Silent", "Mysterious", "Clever"]

def get_comment_count(conf_id: int) -> int:
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) FROM comments WHERE conf_id=?", (conf_id,))
    count = cursor.fetchone()[0]
    db.close()
    return count

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

# ================= 5. START / CATEGORY =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    args = message.text.split()

    if len(args) > 1 and args[1].startswith("reply_"):
        try:
            conf_id = int(args[1].split("_")[1])
            await state.clear()
            
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "SELECT text, file_id, file_type, category, channel_msg_id FROM confessions WHERE id=?", 
                (conf_id,)
            )
            row = cursor.fetchone()
            db.close()

            if not row:
                await message.answer("⚠️ Confession not found.")
                return

            text, file_id, file_type, category, channel_msg_id = row
            hashtags = category_to_hashtags(category)
            comment_count = get_comment_count(conf_id)

            card_text = f"Confession #{conf_id}\n\n{text}\n\n{hashtags}"

            kb = InlineKeyboardBuilder()
            kb.button(text="➕ Add Comment", callback_data=f"add_comment:{conf_id}")
            
            comments_url = f"https://t.me/{CHANNEL_PUBLIC_NAME}/{channel_msg_id}?comment=1"
            kb.button(text=f"💬 Browse Comments ({comment_count})", url=comments_url)
            kb.adjust(1)

            if file_type == "photo":
                await message.answer_photo(photo=file_id, caption=card_text, reply_markup=kb.as_markup())
            elif file_type == "video":
                await message.answer_video(video=file_id, caption=card_text, reply_markup=kb.as_markup())
            else:
                await message.answer(text=card_text, reply_markup=kb.as_markup())
            return
        except Exception as e:
            logging.error(f"Reply link error: {e}")
            await message.answer("⚠️ Broken reply link.")
            return

    await state.clear()
    kb = InlineKeyboardBuilder()
    for cat in CATEGORIES:
        kb.button(text=cat, callback_data=f"select_cat:{cat}")
    kb.adjust(2)
    await state.set_state(BotStates.choosing_category)
    await message.answer(
        "Choose a category for your confession submission:",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("add_comment:"))
async def start_writing_comment(callback: types.CallbackQuery, state: FSMContext):
    conf_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(target_conf_id=conf_id)
    await state.set_state(BotStates.writing_comment)
    
    identity = get_or_create_identity(conf_id, callback.from_user.id)
    
    await callback.message.answer(
        f"🎭 You are posting as **{identity}**.\n\n"
        f"Write your comment and it will appear in the confession thread:"
    )
    await callback.answer()

@dp.callback_query(BotStates.choosing_category, F.data.startswith("select_cat:"))
async def process_category(callback: types.CallbackQuery, state: FSMContext):
    selected_cat = callback.data.split(":")[1]
    await state.update_data(chosen_category=selected_cat)
    await state.set_state(BotStates.writing_confession)
    await callback.message.edit_text(
        f"Selected Category: **{selected_cat}**\n\n"
        f"Now type your confession or send a photo/video:"
    )
    await callback.answer()

# ================= 6. COMMENT HANDLER =================
@dp.message(BotStates.writing_comment, F.chat.type == "private")
async def process_threaded_comment(message: types.Message, state: FSMContext):
    state_data = await state.get_data()
    conf_id = state_data.get("target_conf_id")

    if not conf_id:
        await message.answer("⚠️ Session lost. Please use the reply link again.")
        await state.clear()
        return

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT discussion_chat_id, channel_msg_id FROM confessions WHERE id=?",
        (conf_id,)
    )
    row = cursor.fetchone()
    db.close()

    disc_chat_id = row[0] if row else None
    channel_msg_id = row[1] if row else None

    if not disc_chat_id:
        try:
            chat = await bot.get_chat(CHANNEL_TARGET)
            disc_chat_id = getattr(chat, 'linked_chat_id', None)
            if disc_chat_id:
                db = get_db()
                cursor = db.cursor()
                cursor.execute(
                    "UPDATE confessions SET discussion_chat_id=? WHERE id=?",
                    (disc_chat_id, conf_id)
                )
                db.commit()
                db.close()
        except Exception as e:
            logging.error(f"Failed to auto-retrieve linked chat: {e}")

    if not disc_chat_id or not channel_msg_id:
        await message.answer(
            "⚠️ Could not link your comment to the channel thread.\n\n"
            "**Required setup steps:**\n"
            "1. Link a **Discussion Group** to your channel.\n"
            "2. Add this bot as an **Admin** in that Discussion Group."
        )
        await state.clear()
        return

    identity = get_or_create_identity(conf_id, message.from_user.id)

    try:
        sent = await bot.send_message(
            chat_id=disc_chat_id,
            text=f"💬 **{identity}**:\n\n{message.text}",
            reply_parameters=ReplyParameters(
                message_id=channel_msg_id,
                chat_id=CHANNEL_TARGET
            )
        )

        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO comments (conf_id, chat_id, msg_id) VALUES (?, ?, ?)",
            (conf_id, disc_chat_id, sent.message_id)
        )
        db.commit()
        db.close()

        try:
            comment_count = get_comment_count(conf_id)
            kb_updated = InlineKeyboardBuilder()
            
            kb_updated.button(
                text=f"💬 Confess ({comment_count})", 
                url=f"https://t.me/{BOT_USERNAME}?start=reply_{conf_id}"
            )
            kb_updated.adjust(1)
            await bot.edit_message_reply_markup(
                chat_id=CHANNEL_TARGET,
                message_id=channel_msg_id,
                reply_markup=kb_updated.as_markup()
            )
        except Exception as e:
            logging.warning(f"Could not refresh channel post comments markup: {e}")

        await message.answer("🚀 Your anonymous comment has been posted to the confession thread!")
        logging.info(f"✅ Comment posted: conf_id={conf_id} identity={identity}")

    except Exception as e:
        logging.error(f"Comment submission failed: {e}")
        await message.answer(
            "❌ Failed to submit comment.\n"
            "Please make sure the bot is an Admin with post/send privileges in your channel's Discussion Group."
        )

    await state.clear()

# ================= 7. CONFESSION SUBMISSION =================
@dp.message(BotStates.writing_confession, F.chat.type == "private")
async def handle_submission(message: types.Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("chosen_category", "General")

    text = message.text or message.caption or ""
    file_id, file_type = None, None

    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"

    if not text and not file_id:
        await message.answer("⚠️ Please submit text, a photo, or a video.")
        return

    env_admin_id = os.getenv("ADMIN_GROUP_ID", "-1003923693636")
    try:
        admin_chat_target = int(env_admin_id)
    except ValueError:
        admin_chat_target = env_admin_id

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
    kb.adjust(2)

    admin_caption = f"🏷️ Category: **{category}**\n🆔 Queue ID: `#{conf_id}`\n\n📝 **Confession:**\n{text}"

    try:
        if file_type == "photo":
            await bot.send_photo(chat_id=admin_chat_target, photo=file_id, caption=admin_caption, reply_markup=kb.as_markup())
        elif file_type == "video":
            await bot.send_video(chat_id=admin_chat_target, video=file_id, caption=admin_caption, reply_markup=kb.as_markup())
        else:
            await bot.send_message(chat_id=admin_chat_target, text=admin_caption, reply_markup=kb.as_markup())
        logging.info(f"📬 Confession #{conf_id} sent to admin.")
    except Exception as e:
        logging.error(f"❌ Admin forward failed: {e}")

    await message.answer("📥 Submitted anonymously! Pending admin review.")
    await state.clear()

# ================= 8. FALLBACK =================
@dp.message(F.chat.type == "private")
async def fallback_private(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == BotStates.writing_comment.state:
        await process_threaded_comment(message, state)
        return

    await state.clear()
    kb = InlineKeyboardBuilder()
    for cat in CATEGORIES:
        kb.button(text=cat, callback_data=f"select_cat:{cat}")
    kb.adjust(2)
    await state.set_state(BotStates.choosing_category)
    await message.answer(
        "Choose a category for your confession:",
        reply_markup=kb.as_markup()
    )

# ================= 9. MODERATION =================
@dp.callback_query(F.data.startswith("adm_approve:"))
async def approve_confession(callback: types.CallbackQuery):
    conf_id = int(callback.data.split(":")[1])

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT text, file_id, file_type, category FROM confessions WHERE id=?", (conf_id,))
    row = cursor.fetchone()

    if not row:
        await callback.answer("Confession not found.")
        db.close()
        return

    text, file_id, file_type, category = row
    hashtags = category_to_hashtags(category)
    public_text = f"**Confession #{conf_id}**\n\n{text}\n\n{hashtags}"

    kb = InlineKeyboardBuilder()
    comment_count = get_comment_count(conf_id)
    kb.button(
        text=f"💬 Confess ({comment_count})", 
        url=f"https://t.me/{BOT_USERNAME}?start=reply_{conf_id}"
    )
    kb.adjust(1)

    try:
        if file_type == "photo":
            out = await bot.send_photo(chat_id=CHANNEL_TARGET, photo=file_id, caption=public_text, reply_markup=kb.as_markup())
        elif file_type == "video":
            out = await bot.send_video(chat_id=CHANNEL_TARGET, video=file_id, caption=public_text, reply_markup=kb.as_markup())
        else:
            out = await bot.send_message(chat_id=CHANNEL_TARGET, text=public_text, reply_markup=kb.as_markup())
    except Exception as e:
        logging.error(f"❌ FAILED TO SEND TO CHANNEL {CHANNEL_TARGET}: {e}")
        await callback.answer(f"❌ Error: Could not send to channel. Make sure bot is an admin inside {CHANNEL_TARGET}.", show_alert=True)
        db.close()
        return

    cursor.execute("UPDATE confessions SET channel_msg_id=? WHERE id=?", (out.message_id, conf_id))
    db.commit()

    # Automatically pin approved post in the channel dynamically using out.chat.id
    try:
        await bot.pin_chat_message(
            chat_id=out.chat.id,
            message_id=out.message_id,
            disable_notification=True
        )
        logging.info(f"📌 Pinned approved confession #{conf_id} in channel.")
    except Exception as e:
        logging.error(f"Failed to automatically pin post in channel: {e}")

    try:
        chat = await bot.get_chat(CHANNEL_TARGET)
        linked_chat_id = getattr(chat, 'linked_chat_id', None)
        if linked_chat_id:
            cursor.execute(
                "UPDATE confessions SET discussion_chat_id=? WHERE id=?",
                (linked_chat_id, conf_id)
            )
            db.commit()
            logging.info(f"✅ Sync successful: conf_id={conf_id} linked with discussion={linked_chat_id}")
    except Exception as e:
        logging.error(f"Sync failed during approval: {e}")

    db.close()

    try:
        if callback.message.photo or callback.message.video:
            await callback.message.edit_caption(caption=f"✅ Approved! ID: #{conf_id}")
        else:
            await callback.message.edit_text(text=f"✅ Approved! ID: #{conf_id}")
    except Exception:
        pass

    await callback.answer("Published!")

@dp.callback_query(F.data.startswith("adm_reject:"))
async def reject_confession(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Rejected.")

# Silent legacy handler
@dp.callback_query(F.data.startswith("react:"))
async def handle_reactions(callback: types.CallbackQuery):
    await callback.answer("Reactions are deactivated.")

# ================= 10. DISCUSSION GROUP SYNC =================
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def catch_discussion_mirror(message: types.Message):
    try:
        orig_msg_id = None

        if message.forward_from_chat:
            fc = message.forward_from_chat
            if fc.username and fc.username.lstrip("@").lower() == CHANNEL_PUBLIC_NAME.lower():
                orig_msg_id = message.forward_from_message_id

        if not orig_msg_id and message.forward_origin:
            fo = message.forward_origin
            if hasattr(fo, "chat") and hasattr(fo, "message_id"):
                uname = getattr(fo.chat, "username", "") or ""
                if uname.lstrip("@").lower() == CHANNEL_PUBLIC_NAME.lower():
                    orig_msg_id = fo.message_id

        if orig_msg_id:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "UPDATE confessions SET discussion_chat_id=?, discussion_msg_id=? WHERE channel_msg_id=?",
                (message.chat.id, message.message_id, orig_msg_id)
            )
            db.commit()
            db.close()

    except Exception as e:
        logging.error(f"Sync error: {e}")

# ================= 11. LIFESPAN =================
token_string = os.getenv("API_TOKEN", "")
STATIC_WEBHOOK_PATH = f"/webhook/{token_string[:10]}" if token_string else "/webhook/default"

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot
    token = os.getenv("API_TOKEN")
    url = os.getenv("RENDER_URL", "https://wku-confession-bot-8aoc.onrender.com")
    if not token:
        raise RuntimeError("Missing API_TOKEN")
    bot = Bot(token=token)
    target_webhook_url = f"{url}/webhook/{token[:10]}"
    await bot.set_webhook(
        url=target_webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "channel_post", "edited_channel_post"]
    )
    logging.info(f"✅ Webhook set: {target_webhook_url}")
    yield
    if bot:
        await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.post(STATIC_WEBHOOK_PATH)
async def process_webhook_payload(request: Request):
    if bot is not None:
        try:
            payload = await request.json()
            update = types.Update.model_validate(payload, context={"bot": bot})
            await dp.feed_update(bot, update)
        except Exception as e:
            logging.error(f"Webhook processing error: {e}")
    return {"ok": True}

@app.get("/")
def read_root():
    return {"status": "operational", "engine": "aiogram3"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), factory=False)
