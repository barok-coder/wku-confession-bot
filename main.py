import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI
import uvicorn
import os

# --- CONFIG ---
# We use os.environ to keep your secret token safe on Render!
API_TOKEN = os.environ.get("BOT_TOKEN", "8857559349:AAFGI_hxQ3MI04cFbHbzIIgh1QU-DGkuCJ4")
CHANNEL_ID = "@wku_confessions_official" 
BOT_USERNAME = "wku_confessionsbot"
ADMIN_GROUP_ID = -1001234567890 # Put your real Admin numeric group ID here!

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
confessions_db = {} 
confession_counter = 1

class BotStates(StatesGroup):
    waiting_for_confession = State()
    waiting_for_comment = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("reply_"):
        conf_id = args[1].replace("reply_", "")
        await state.update_data(target_conf_id=conf_id)
        await state.set_state(BotStates.waiting_for_comment)
        await message.answer(f"✍️ Replying anonymously to Confession #{conf_id}.\nSend your comment below:")
        return
    await state.set_state(BotStates.waiting_for_confession)
    await message.answer("Welcome to WKU Confessions! 🤫\nSend your confession as text or photo.")

@dp.message(BotStates.waiting_for_confession, F.text | F.photo)
async def process_confession_submission(message: types.Message, state: FSMContext):
    global confession_counter
    conf_id = str(confession_counter)
    confession_counter += 1
    confessions_db[conf_id] = {
        "text": message.text if message.text else message.caption,
        "photo_id": message.photo[-1].file_id if message.photo else None,
        "sender_id": message.from_user.id
    }
    preview_text = confessions_db[conf_id]["text"] or "[Photo Content]"
    admin_caption = f"🚨 **New Confession**\nID: #{conf_id}\n\n{preview_text}"
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Approve", callback_data=f"approve_{conf_id}")
    builder.button(text="❌ Reject", callback_data=f"reject_{conf_id}")
    
    if confessions_db[conf_id]["photo_id"]:
        await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=confessions_db[conf_id]["photo_id"], caption=admin_caption, reply_markup=builder.as_markup())
    else:
        await bot.send_message(chat_id=ADMIN_GROUP_ID, text=admin_caption, reply_markup=builder.as_markup())
    await message.answer("📥 Sent to admins for review!")
    await state.clear()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_callback(callback: types.CallbackQuery):
    conf_id = callback.data.replace("approve_", "")
    data = confessions_db.get(conf_id)
    if not data: return
    comment_builder = InlineKeyboardBuilder()
    comment_builder.button(text="💬 Leave an Anonymous Comment", url=f"https://t.me/{BOT_USERNAME}?start=reply_{conf_id}")
    channel_post_text = f"📢 **WKU Confession #{conf_id}**\n\n{data['text'] or ''}"
    if data["photo_id"]:
        await bot.send_photo(chat_id=CHANNEL_ID, photo=data["photo_id"], caption=channel_post_text, reply_markup=comment_builder.as_markup())
    else:
        await bot.send_message(chat_id=CHANNEL_ID, text=channel_post_text, reply_markup=comment_builder.as_markup())
    await callback.message.edit_caption(caption=f"✅ Approved!\nID: #{conf_id}")

@dp.callback_query(F.data.startswith("reject_"))
async def reject_callback(callback: types.CallbackQuery):
    await callback.message.delete()

@dp.message(BotStates.waiting_for_comment, F.text)
async def process_anonymous_comment(message: types.Message, state: FSMContext):
    state_data = await state.get_data()
    conf_id = state_data.get("target_conf_id")
    comment_text = f"💬 **Anonymous Comment on #{conf_id}:**\n\n{message.text}"
    await bot.send_message(chat_id=CHANNEL_ID, text=comment_text)
    await message.answer("🚀 Comment published!")
    await state.clear()

# Tiny Web Service so Render stays awake
app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "alive"}

async def run_bot():
    await dp.start_polling(bot)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(run_bot())
    # Render provides a specific port variable automatically
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)