import asyncio
import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI
import uvicorn

# ================= CONFIG =================

API_TOKEN = os.getenv("API_TOKEN")

CHANNEL_ID = "@wku_confessions_official"
ADMIN_GROUP = -1003923693636   # your linked discussion group ID

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

cur.execute("""
CREATE TABLE IF NOT EXISTS confessions (
id INTEGER PRIMARY KEY,
text TEXT,
photo TEXT,
channel_msg_id INTEGER
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

# ================= START =================

@dp.message(Command("start"))
async def start(m: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(S.wait_conf)
    await m.answer("Send confession anonymously.")

# ================= SAVE CONFESSION =================

@dp.message(S.wait_conf, F.text | F.photo)
async def save_conf(m: types.Message):
    text = m.text or m.caption
    photo = m.photo[-1].file_id if m.photo else None

    cur.execute("INSERT INTO confessions(text,photo,channel_msg_id) VALUES(?,?,?)",
                (text, photo, None))
    db.commit()

    await m.answer("Sent for review.")

# ================= ADMIN APPROVE =================

@dp.callback_query(F.data.startswith("approve_"))
async def approve(c: types.CallbackQuery):
    cid = int(c.data.split("_")[1])

    cur.execute("SELECT text,photo FROM confessions WHERE id=?", (cid,))
    row = cur.fetchone()

    text, photo = row

    kb = InlineKeyboardBuilder()
    kb.button(text="💬 Comment", url=f"https://t.me/{BOT_USERNAME}?start=comment_{cid}")
    kb.button(text="👍", callback_data=f"like_{cid}")
    kb.button(text="👎", callback_data=f"dislike_{cid}")
    kb.adjust(1)

    if photo:
        msg = await bot.send_photo(CHANNEL_ID, photo, caption=text, reply_markup=kb.as_markup())
    else:
        msg = await bot.send_message(CHANNEL_ID, text, reply_markup=kb.as_markup())

    cur.execute("UPDATE confessions SET channel_msg_id=? WHERE id=?", (msg.message_id, cid))
    db.commit()

    await c.answer("Approved")

# ================= DISCUSSION TRACKING =================

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def track_discussion(m: types.Message):
    if m.is_automatic_forward:
        orig = m.forward_from_message_id

        cur.execute("SELECT id FROM confessions WHERE channel_msg_id=?", (orig,))
        r = cur.fetchone()
        if not r:
            return

        logging.info(f"Linked confession {r[0]} -> discussion {m.message_id}")

# ================= COMMENT SYSTEM =================

@dp.message(Command("start"))
async def start(m: types.Message, state: FSMContext):
    args = m.text.split()

    if len(args) > 1 and args[1].startswith("comment_"):
        cid = int(args[1].split("_")[1])
        await state.update_data(conf_id=cid)
        await state.set_state(S.wait_comment)
        await m.answer("Send comment:")
        return

    await state.clear()
    await state.set_state(S.wait_conf)
    await m.answer("Send confession anonymously.")


@dp.message(S.wait_comment, F.text)
async def comment(m: types.Message, state: FSMContext):
    data = await state.get_data()
    cid = data["conf_id"]

    cur.execute("SELECT channel_msg_id FROM confessions WHERE id=?", (cid,))
    row = cur.fetchone()
    if not row:
        return

    channel_msg_id = row[0]

    sent = await bot.send_message(
        ADMIN_GROUP,
        f"💬 {m.text}",
        reply_to_message_id=channel_msg_id
    )

    cur.execute("""
    INSERT INTO comments(conf_id,parent_id,chat_id,msg_id,text)
    VALUES(?,?,?,?,?)
    """, (cid, None, ADMIN_GROUP, sent.message_id, m.text))

    db.commit()

    await m.answer("Comment posted")
    await state.clear()

# ================= FASTAPI =================

app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {"status": "alive"}

@app.post(WEBHOOK_PATH)
async def webhook(update: dict):
    tg = types.Update.model_validate(update, context={"bot": bot})
    await dp.feed_update(bot, tg)
    return {"ok": True}

# ================= STARTUP =================

@app.on_event("startup")
async def start_up():
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    logging.info("Bot started")

@app.on_event("shutdown")
async def shutdown():
    await bot.session.close()

# ================= RUN =================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
