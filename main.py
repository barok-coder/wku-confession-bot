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

# --- CONFIG ---
API_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = "@wku_confessions_official" 
BOT_USERNAME = "wku_confessionsbot"
ADMIN_GROUP_ID = os.environ.get("ADMIN_GROUP_ID")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Memory tracking to link confessions to channel posts and discussion posts
confessions_db = {} 
confession_counter = 1

class BotStates(StatesGroup):
    waiting_for_confession = State()
    waiting_for_comment = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    args = message.text.split()
    
    # Handle user clicking "+ Add Comment" from the channel post
    if len(args) > 1 and args[1].startswith("comm_"):
        channel_post_id = args[1].replace("comm_", "")
        await state.update_data(target_post_id=int(channel_post_id))
        await state.set_state(BotStates.waiting_for_comment)
        await message.answer("✍️ Send your anonymous comment below:")
        return

    await state.set_state(BotStates.waiting_for_confession)
    await message.answer("Welcome to WKU Confessions! 🤫\nSend your confession text or photo:")

async def send_to_admins(message: types.Message):
    global confession_counter
    conf_id = str(confession_counter)
    confession_counter += 1
    
    text_content = message.text if message.text else message.caption
    photo_id = message.photo[-1].file_id if message.photo else None
    
    confessions_db[conf_id] = {
        "text": text_content,
        "photo_id": photo_id,
        "likes": 0,
        "dislikes": 0,
        "liked_users": set(),
        "disliked_users": set(),
        "channel_message_id": None
    }
    
    preview_text = text_content or "[Photo Content]"
    admin_caption = f"🚨 **New Confession**\nID: #{conf_id}\n\n{preview_text}"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Approve", callback_data=f"approve_{conf_id}")
    builder.button(text="❌ Reject", callback_data=f"reject_{conf_id}")
    
    if photo_id:
        await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=photo_id, caption=admin_caption, reply_markup=builder.as_markup())
    else:
        await bot.send_message(chat_id=ADMIN_GROUP_ID, text=admin_caption, reply_markup=builder.as_markup())
    await message.answer("📥 Sent to admins for review!")

@dp.message(BotStates.waiting_for_confession, F.text | F.photo)
async def process_confession_submission(message: types.Message, state: FSMContext):
    await send_to_admins(message)
    await state.clear()

def create_channel_keyboard(conf_id, message_id):
    data = confessions_db[conf_id]
    builder = InlineKeyboardBuilder()
    
    # + Add Comment button points directly to the specific post message ID
    builder.button(text="➕ Add Comment", url=f"https://t.me/{BOT_USERNAME}?start=comm_{message_id}")
    builder.button(text=f"👍 {data['likes']}", callback_data=f"like_{conf_id}")
    builder.button(text=f"👎 {data['dislikes']}", callback_data=f"dislike_{conf_id}")
    
    builder.adjust(1, 2)
    return builder.as_markup()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_callback(callback: types.CallbackQuery):
    conf_id = callback.data.replace("approve_", "")
    data = confessions_db.get(conf_id)
    if not data: return
    
    channel_post_text = f"📢 **WKU Confession #{conf_id}**\n\n{data['text'] or ''}"
    
    # Post temporary version to get the message_id safely
    if data["photo_id"]:
        sent = await bot.send_photo(chat_id=CHANNEL_ID, photo=data["photo_id"], caption=channel_post_text)
    else:
        sent = await bot.send_message(chat_id=CHANNEL_ID, text=channel_post_text)
    
    # Save the real message ID and append our clean feedback interface
    data["channel_message_id"] = sent.message_id
    markup = create_channel_keyboard(conf_id, sent.message_id)
    
    if data["photo_id"]:
        await bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=sent.message_id, caption=channel_post_text, reply_markup=markup)
    else:
        await bot.edit_message_text(chat_id=CHANNEL_ID, message_id=sent.message_id, text=channel_post_text, reply_markup=markup)
    
    try:
        if callback.message.photo:
            await callback.message.edit_caption(caption=f"✅ Approved!\nID: #{conf_id}")
        else:
            await callback.message.edit_text(text=f"✅ Approved!\nID: #{conf_id}")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("reject_"))
async def reject_callback(callback: types.CallbackQuery):
    await callback.message.delete()

# --- INTERACTIVE VOTING LOGIC ---
@dp.callback_query(F.data.startswith("like_"))
async def like_post(callback: types.CallbackQuery):
    conf_id = callback.data.replace("like_", "")
    user_id = callback.from_user.id
    data = confessions_db.get(conf_id)
    if not data or not data["channel_message_id"]: return
    
    if user_id in data["liked_users"]:
        data["liked_users"].remove(user_id)
        data["likes"] -= 1
    else:
        data["liked_users"].add(user_id)
        data["likes"] += 1
        if user_id in data["disliked_users"]:
            data["disliked_users"].remove(user_id)
            data["dislikes"] -= 1
            
    await callback.message.edit_reply_markup(reply_markup=create_channel_keyboard(conf_id, data["channel_message_id"]))
    await callback.answer()

@dp.callback_query(F.data.startswith("dislike_"))
async def dislike_post(callback: types.CallbackQuery):
    conf_id = callback.data.replace("dislike_", "")
    user_id = callback.from_user.id
    data = confessions_db.get(conf_id)
    if not data or not data["channel_message_id"]: return
    
    if user_id in data["disliked_users"]:
        data["disliked_users"].remove(user_id)
        data["dislikes"] -= 1
    else:
        data["disliked_users"].add(user_id)
        data["dislikes"] += 1
        if user_id in data["liked_users"]:
            data["liked_users"].remove(user_id)
            data["likes"] -= 1
            
    await callback.message.edit_reply_markup(reply_markup=create_channel_keyboard(conf_id, data["channel_message_id"]))
    await callback.answer()

# --- THE NATIVE NESTED REPLY MAGIC ---
@dp.message(BotStates.waiting_for_comment, F.text)
async def process_anonymous_comment(message: types.Message, state: FSMContext):
    state_data = await state.get_data()
    target_post_id = state_data.get("target_post_id")
    
    try:
        # We forward the message directly into the channel, but passing the post's message_id
        # as a reply_to_message_id parameter. Telegram handles the rest!
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"💬 {message.text}",
            reply_to_message_id=target_post_id
        )
        await message.answer("🚀 Your anonymous comment has been posted inside the replies section!")
    except Exception as e:
        logging.error(f"Failed to post native reply: {e}")
        await message.answer("❌ Error posting comment. Make sure discussion group is linked.")
        
    await state.clear()

@dp.message(F.text | F.photo)
async def fallback_confession_handler(message: types.Message, state: FSMContext):
    await send_to_admins(message)
    await state.clear()

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "alive"}

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
