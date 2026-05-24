import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from fastapi import FastAPI
import uvicorn

# =========================================================
# CONFIG
# =========================================================

API_TOKEN = os.environ.get("BOT_TOKEN")

CHANNEL_ID = "@wku_confessions_official"
ADMIN_GROUP_ID = "@wku_admins_review_team"

# YOUR REAL BOT USERNAME (NO @)
BOT_USERNAME = "@wku_confessionsbot"

RENDER_EXTERNAL_URL = "https://wku-confession-bot-8aoc.onrender.com"

logging.basicConfig(level=logging.INFO)

# =========================================================
# BOT SETUP
# =========================================================

bot = Bot(token=API_TOKEN)

storage = MemoryStorage()

dp = Dispatcher(storage=storage)

# =========================================================
# DATABASES
# =========================================================

confessions_db = {}

comments_db = {}

confession_counter = 1

comment_counter = 1

# =========================================================
# STATES
# =========================================================

class BotStates(StatesGroup):

    waiting_for_confession = State()

    waiting_for_comment = State()

# =========================================================
# START COMMAND
# =========================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):

    await state.clear()

    args = message.text.split()

    # ==========================================
    # ADD COMMENT TO CONFESSION
    # ==========================================

    if len(args) > 1 and args[1].startswith("comm_"):

        conf_id = args[1].replace("comm_", "")

        if conf_id not in confessions_db:

            await message.answer("❌ Confession not found.")

            return

        await state.update_data(
            target_conf_id=conf_id,
            reply_to_comment=None
        )

        await state.set_state(BotStates.waiting_for_comment)

        await message.answer(
            f"💬 Replying anonymously to Confession #{conf_id}\n\n"
            f"Send your comment:"
        )

        return

    # ==========================================
    # REPLY TO COMMENT
    # ==========================================

    if len(args) > 1 and args[1].startswith("reply_"):

        comment_id = args[1].replace("reply_", "")

        comment_data = comments_db.get(comment_id)

        if not comment_data:

            await message.answer("❌ Comment not found.")

            return

        await state.update_data(
            target_conf_id=comment_data["confession_id"],
            reply_to_comment=comment_id
        )

        await state.set_state(BotStates.waiting_for_comment)

        await message.answer(
            "↩️ Send your anonymous reply:"
        )

        return

    # ==========================================
    # NORMAL CONFESSION MODE
    # ==========================================

    await state.set_state(BotStates.waiting_for_confession)

    await message.answer(
        "🤫 Welcome to WKU Confessions!\n\n"
        "Send your anonymous confession text or photo."
    )

# =========================================================
# SEND CONFESSION TO ADMINS
# =========================================================

async def send_to_admins(message: types.Message):

    global confession_counter

    conf_id = str(confession_counter)

    confession_counter += 1

    text_content = message.text if message.text else message.caption

    photo_id = None

    if message.photo:

        photo_id = message.photo[-1].file_id

    confessions_db[conf_id] = {
        "text": text_content,
        "photo_id": photo_id,
        "likes": 0,
        "dislikes": 0,
        "liked_users": set(),
        "disliked_users": set(),
        "channel_message_id": None,
        "discussion_chat_id": None,
        "discussion_message_id": None
    }

    preview = text_content if text_content else "[Photo Content]"

    admin_text = (
        f"🚨 NEW CONFESSION\n\n"
        f"ID: #{conf_id}\n\n"
        f"{preview}"
    )

    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Approve",
        callback_data=f"approve_{conf_id}"
    )

    builder.button(
        text="❌ Reject",
        callback_data=f"reject_{conf_id}"
    )

    markup = builder.as_markup()

    if photo_id:

        await bot.send_photo(
            chat_id=ADMIN_GROUP_ID,
            photo=photo_id,
            caption=admin_text,
            reply_markup=markup
        )

    else:

        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=admin_text,
            reply_markup=markup
        )

    await message.answer(
        "📥 Your confession has been submitted for review."
    )

# =========================================================
# PRIVATE CONFESSION HANDLER
# =========================================================

@dp.message(F.chat.type == "private", F.text | F.photo)
async def private_handler(
    message: types.Message,
    state: FSMContext
):

    current_state = await state.get_state()

    # COMMENT MODE
    if current_state == BotStates.waiting_for_comment:

        return

    await send_to_admins(message)

    await state.clear()

# =========================================================
# CHANNEL BUTTONS
# =========================================================

def create_channel_keyboard(conf_id):

    data = confessions_db[conf_id]

    builder = InlineKeyboardBuilder()

    builder.button(
        text="➕ Add Comment",
        url=f"https://t.me/{BOT_USERNAME}?start=comm_{conf_id}"
    )

    builder.button(
        text=f"👍 {data['likes']}",
        callback_data=f"like_{conf_id}"
    )

    builder.button(
        text=f"👎 {data['dislikes']}",
        callback_data=f"dislike_{conf_id}"
    )

    builder.adjust(1, 2)

    return builder.as_markup()

# =========================================================
# APPROVE CONFESSION
# =========================================================

@dp.callback_query(F.data.startswith("approve_"))
async def approve_callback(callback: types.CallbackQuery):

    conf_id = callback.data.replace("approve_", "")

    data = confessions_db.get(conf_id)

    if not data:

        return

    post_text = (
        f"📢 WKU Confession #{conf_id}\n\n"
        f"{data['text'] or ''}"
    )

    markup = create_channel_keyboard(conf_id)

    # SEND PHOTO POST
    if data["photo_id"]:

        sent = await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=data["photo_id"],
            caption=post_text,
            reply_markup=markup
        )

    # SEND TEXT POST
    else:

        sent = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=post_text,
            reply_markup=markup
        )

    data["channel_message_id"] = sent.message_id

    logging.info(
        f"Posted confession #{conf_id} "
        f"-> Channel Message {sent.message_id}"
    )

    try:

        if callback.message.photo:

            await callback.message.edit_caption(
                caption=f"✅ Approved\n\nConfession #{conf_id}"
            )

        else:

            await callback.message.edit_text(
                text=f"✅ Approved\n\nConfession #{conf_id}"
            )

    except Exception as e:

        logging.error(e)

# =========================================================
# REJECT CONFESSION
# =========================================================

@dp.callback_query(F.data.startswith("reject_"))
async def reject_callback(callback: types.CallbackQuery):

    try:

        await callback.message.delete()

    except:

        pass

# =========================================================
# DETECT DISCUSSION AUTO FORWARD
# =========================================================

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def detect_discussion_forward(message: types.Message):

    try:

        if message.is_automatic_forward:

            original_channel_msg_id = message.forward_from_message_id

            logging.info(
                f"FORWARD DETECTED | "
                f"Channel Msg {original_channel_msg_id} "
                f"-> Discussion Msg {message.message_id}"
            )

            for conf_id, data in confessions_db.items():

                if data["channel_message_id"] == original_channel_msg_id:

                    data["discussion_chat_id"] = message.chat.id

                    data["discussion_message_id"] = message.message_id

                    logging.info(
                        f"MATCHED Confession #{conf_id}"
                    )

                    return

    except Exception as e:

        logging.error(f"Discussion mapping error: {e}")

# =========================================================
# PROCESS COMMENTS + REPLIES
# =========================================================

@dp.message(BotStates.waiting_for_comment, F.text)
async def process_comment(
    message: types.Message,
    state: FSMContext
):

    global comment_counter

    state_data = await state.get_data()

    conf_id = state_data.get("target_conf_id")

    reply_to_comment = state_data.get("reply_to_comment")

    # WAIT FOR DISCUSSION SYNC
    for retry in range(10):

        data = confessions_db.get(conf_id)

        if data and data.get("discussion_message_id"):

            break

        await asyncio.sleep(1.5)

    data = confessions_db.get(conf_id)

    if not data or not data.get("discussion_message_id"):

        await message.answer(
            "⚠️ Telegram is syncing the post.\nTry again in a few seconds."
        )

        await state.clear()

        return

    try:

        # DEFAULT TARGET = MAIN POST
        target_message_id = data["discussion_message_id"]

        # REPLY TO COMMENT
        if reply_to_comment:

            target_message_id = comments_db[reply_to_comment]["message_id"]

        # SEND MESSAGE
        sent = await bot.send_message(
            chat_id=data["discussion_chat_id"],
            text=f"💬 Anonymous:\n\n{message.text}",
            reply_to_message_id=target_message_id
        )

        # SAVE COMMENT
        comment_id = str(comment_counter)

        comments_db[comment_id] = {
            "confession_id": conf_id,
            "message_id": sent.message_id
        }

        comment_counter += 1

        # ADD REPLY BUTTON
        builder = InlineKeyboardBuilder()

        builder.button(
            text="↩️ Reply",
            url=f"https://t.me/{BOT_USERNAME}?start=reply_{comment_id}"
        )

        await bot.edit_message_reply_markup(
            chat_id=data["discussion_chat_id"],
            message_id=sent.message_id,
            reply_markup=builder.as_markup()
        )

        await message.answer(
            "✅ Anonymous comment posted."
        )

    except Exception as e:

        logging.error(e)

        await message.answer(
            "❌ Failed to post comment."
        )

    await state.clear()

# =========================================================
# LIKE SYSTEM
# =========================================================

@dp.callback_query(F.data.startswith("like_"))
async def like_post(callback: types.CallbackQuery):

    conf_id = callback.data.replace("like_", "")

    user_id = callback.from_user.id

    data = confessions_db.get(conf_id)

    if not data:

        return

    if user_id in data["liked_users"]:

        data["liked_users"].remove(user_id)

        data["likes"] -= 1

    else:

        data["liked_users"].add(user_id)

        data["likes"] += 1

        if user_id in data["disliked_users"]:

            data["disliked_users"].remove(user_id)

            data["dislikes"] -= 1

    await bot.edit_message_reply_markup(
        chat_id=CHANNEL_ID,
        message_id=data["channel_message_id"],
        reply_markup=create_channel_keyboard(conf_id)
    )

    await callback.answer()

# =========================================================
# DISLIKE SYSTEM
# =========================================================

@dp.callback_query(F.data.startswith("dislike_"))
async def dislike_post(callback: types.CallbackQuery):

    conf_id = callback.data.replace("dislike_", "")

    user_id = callback.from_user.id

    data = confessions_db.get(conf_id)

    if not data:

        return

    if user_id in data["disliked_users"]:

        data["disliked_users"].remove(user_id)

        data["dislikes"] -= 1

    else:

        data["disliked_users"].add(user_id)

        data["dislikes"] += 1

        if user_id in data["liked_users"]:

            data["liked_users"].remove(user_id)

            data["likes"] -= 1

    await bot.edit_message_reply_markup(
        chat_id=CHANNEL_ID,
        message_id=data["channel_message_id"],
        reply_markup=create_channel_keyboard(conf_id)
    )

    await callback.answer()

# =========================================================
# FASTAPI
# =========================================================

app = FastAPI()

WEBHOOK_PATH = f"/webhook/{API_TOKEN}"

WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

@app.api_route("/", methods=["GET", "HEAD"])
async def root():

    return {
        "status": "alive"
    }

@app.post(WEBHOOK_PATH)
async def bot_webhook(update: dict):

    telegram_update = types.Update.model_validate(
        update,
        context={"bot": bot}
    )

    await dp.feed_update(bot, telegram_update)

    return {"ok": True}

# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def on_startup():

    try:

        await bot.set_webhook(
            url=WEBHOOK_URL,
            drop_pending_updates=True,
            allowed_updates=[
                "message",
                "callback_query"
            ]
        )

        logging.info(f"Webhook set: {WEBHOOK_URL}")

    except Exception as e:

        logging.error(f"Webhook setup failed: {e}")

# =========================================================
# SHUTDOWN
# =========================================================

@app.on_event("shutdown")
async def on_shutdown():

    await bot.session.close()

# =========================================================
# RUN SERVER
# =========================================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
