import asyncio
import logging
import os
import sqlite3
import threading

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from fastapi import FastAPI, Request
import uvicorn

# ================= CONFIG =================

API_TOKEN = os.getenv("API_TOKEN")

if not API_TOKEN:
    raise RuntimeError("API_TOKEN is missing in environment variables")

CHANNEL_ID = "@wku_confessions_official"
ADMIN_ID = 123456789  # <--- REPLACE THIS with your personal Telegram User ID
RENDER_URL = "https://wku-confession-bot-8aoc.onrender.com"

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_URL}{WEBHOOK_PATH}"

BOT_USERNAME = "wku_confessionsbot"

logging.basicConfig(level=logging.INFO)

# ================= BOT =================

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================= DATABASE =================

db = sqlite3.connect("confessions.db", check_same_thread=False)
cur = db.cursor()
db_lock = threading.Lock()

with db_lock:
    cur.execute("""
    CREATE TABLE IF NOT EXISTS confessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT,
        photo TEXT,
        channel_msg_id INTEGER,
        discussion_chat_id INTEGER,
        discussion_msg_id INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conf_id INTEGER,
        parent_id INTEGER,
        chat_id INTEGER,
        msg_id INTEGER,
        text TEXT
    )
    """)
    db.commit()

# ================= STATES =================

class S(StatesGroup):
    wait_conf = State()
    wait_comment = State()

# ================= START HANDLER =================

@dp.message(Command("start"))
async def start(m: types.Message, state: FSMContext):
    args = m.text.split()

    # deep link for comments
    if len(args) > 1 and args[1].startswith("comment_"):
        cid = int(args[1].split("_")[1])
        await state.update_data(conf_id=cid)
        await state.set_state(S.wait_comment)
        await m.answer(f"✍️ **You are replying anonymously to Confession #{cid}.**\nSend your comment below:")
        return

    await state.clear()
    await state.set_state(S.wait_conf)
    await m.answer("Welcome to WKU Confessions! 🤫\nSend your confession text or photo right here anonymously:")

# ================= SAVE & FORWARD TO ADMIN PRIVATELY =================

@dp.message(S.wait_conf, F.chat.type == "private", (F.text | F.photo))
async def save_conf(m: types.Message):
    text = m.text or m.caption
    photo = m.photo[-1].file_id if m.photo else None

    with db_lock:
        cur.execute(
            "INSERT INTO confessions(text, photo, channel_msg_id) VALUES(?, ?, ?)",
            (text, photo, None)
        )
        cid = cur.lastrowid
        db.commit()

    # Build Admin Review Card for your Private Chat
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Approve", callback_data=f"approve_{cid}")
    kb.button(text="❌ Reject", callback_data=f"reject_{cid}")
    
    display_text = text if text else "📷 [Photo Confession]"
    admin_text = f"🚨 **New Confession Submitted**\nID: #{cid}\n\n{display_text}"

    try:
        if photo:
            await bot.send_photo(ADMIN_ID, photo, caption=admin_text, reply_markup=kb.as_markup())
        else:
            await bot.send_message(ADMIN_ID, text=admin_text, reply_markup=kb.as_markup())
    except Exception as e:
        logging.error(f"Failed sending layout to Admin ID: {e}")

    await m.answer("📥 Your anonymous confession has been submitted for admin review!")

# ================= ADMIN APPROVE / REJECT =================

@dp.callback_query(F.data.startswith("approve_"))
async def approve(c: types.CallbackQuery):
    cid = int(c.data.split("_")[1])

    with db_lock:
        cur.execute("SELECT text, photo FROM confessions WHERE id=?", (cid,))
        row = cur.fetchone()

    if not row:
        await c.answer("Not found")
        return

    text, photo = row
    channel_post_text = f"📢 **WKU Confession #{cid}**\n\n{text or ''}"

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Add Comment", url=f"https://t.me/{BOT_USERNAME}?start=comment_{cid}")
    kb.button(text="👍", callback_data=f"like_{cid}")
    kb.button(text="👎", callback_data=f"dislike_{cid}")
    kb.adjust(1, 2)

    if photo:
        msg = await bot.send_photo(CHANNEL_ID, photo, caption=channel_post_text, reply_markup=kb.as_markup())
    else:
        msg = await bot.send_message(CHANNEL_ID, channel_post_text, reply_markup=kb.as_markup())

    with db_lock:
        cur.execute("UPDATE confessions SET channel_msg_id=? WHERE id=?", (msg.message_id, cid))
        db.commit()

    try:
        await c.message.edit_caption(caption=f"✅ Approved!\nID: #{cid}") if c.message.photo else await c.message.edit_text(text=f"✅ Approved!\nID: #{cid}")
    except Exception:
        pass
    await c.answer("Approved")

@dp.callback_query(F.data.startswith("reject_"))
async def reject(c: types.CallbackQuery):
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.answer("Rejected")

# ================= AUTOMATIC FORWARD CAPTURE =================

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def catch_discussion_forward(m: types.Message):
    try:
        if m.forward_from_chat and m.forward_from_chat.username == CHANNEL_ID.replace("@", ""):
            orig_msg_id = m.forward_from_message_id
            
            with db_lock:
                cur.execute("UPDATE confessions SET discussion_chat_id=?, discussion_msg_id=? WHERE channel_msg_id=?", 
                            (m.chat.id, m.message_id, orig_msg_id))
                db.commit()
                logging.info(f"🎯 MATCHED: Channel Msg ID {orig_msg_id} mapped to Discussion Chat {m.chat.id} Msg ID {m.message_id}")
    except Exception as e:
        logging.error(f"Error mapping discussion forward: {e}")

# ================= NATIVE COMMENT SYSTEM =================

@dp.message(S.wait_comment, F.text)
async def comment(m: types.Message, state: FSMContext):
    data = await state.get_data()
    cid = data.get("conf_id")

    if not cid:
        await m.answer("Invalid session.")
        return

    for _ in range(3):
        with db_lock:
            cur.execute("SELECT discussion_chat_id, discussion_msg_id FROM confessions WHERE id=?", (cid,))
            row = cur.fetchone()
        if row and row[0] and row[1]:
            break
        await asyncio.sleep(1)

    if not row or not row[0] or not row[1]:
        await m.answer("⚠️ System syncing! Please wait a moment for the post to register and try again.")
        await state.clear()
        return

    disc_chat_id, disc_msg_id = row[0], row[1]

    try:
        sent = await bot.send_message(
            chat_id=disc_chat_id,
            text=f"💬 **Anonymous:**\n\n{m.text}",
            reply_to_message_id=disc_msg_id
        )

        with db_lock:
            cur.execute("""
            INSERT INTO comments(conf_id, parent_id, chat_id, msg_id, text)
            VALUES(?, ?, ?, ?, ?)
            """, (cid, None, disc_chat_id, sent.message_id, m.text))
            db.commit()

        await m.answer("🚀 Your anonymous comment has been posted inside the replies drawer!")
    except Exception as e:
        logging.error(f"Error posting nested reply: {e}")
        await m.answer("❌ Failed to post comment inside the channel's native feed.")
        
    await state.clear()

# ================= FALLBACK FOR MISSED PRIVATE MESSAGES =================

@dp.message(F.chat.type == "private")
async def private_fallback(m: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(S.wait_conf)
    await save_conf(m)

# ================= FASTAPI =================

app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {"status": "alive"}

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    tg = types.Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, tg)
    return {"ok": True}

# ================= STARTUP =================

@app.on_event("startup")
async def startup():
    await bot.set_webhook(
        WEBHOOK_URL,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"]
    )
    logging.info("Bot started via Webhook")

@app.on_event("shutdown")
async def shutdown():
    await bot.session.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
