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

# In-memory mapping to map confession IDs to tracking info
confessions_db = {} 
confession_counter = 1

class BotStates(StatesGroup):
    waiting_for_confession = State()
    waiting_for_comment = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()  # Clear any stuck previous states cleanly
    args = message.text.split()
    
    # Handle user clicking "+ Add Comment" from the channel
    if len(args) > 1 and args[1].startswith("comm_"):
        conf_id = args[1].replace("comm_", "")
        await state.update_data(target_conf_id=conf_id)
        await state.set_state(BotStates.waiting_for_comment)
        await message.answer(f"✍️ **Replying anonymously to Confession #{conf_id}.**\nSend your comment text below:")
        return

    await state.set_state(BotStates.waiting_for_confession)
    await message.answer("Welcome to WKU Confessions! 🤫\nSend your confession text or photo right here:")

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
        "channel_message_id": None,
        "discussion_message_id": None
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

# --- REPLIES INTERCEPTION FOR DISCUSSION GROUPS ---
# This ensures it ONLY listens to group messages, never blocking user confessions
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def catch_discussion_forward(message: types.Message):
    """Intercepts and maps the channel's automatically forwarded post inside the discussion group"""
    try:
        # Check if this message was automatically forwarded from your channel
        if message.forward_from_chat and message.forward_from_chat.username == CHANNEL_ID.replace("@", ""):
            orig_msg_id = message.forward_from_message_id
            
            # Loop through our database to find the confession that matches this channel message ID
            for conf_id, data in confessions_db.items():
                if data.get("channel_message_id") == orig_msg_id:
                    data["discussion_chat_id"] = message.chat.id
                    data["discussion_message_id"] = message.message_id
                    logging.info(f"🎯 MATCHED: Confession #{conf_id} linked to Group Message ID {message.message_id}")
                    return
    except Exception as e:
        logging.error(f"Error mapping discussion chat forward: {e}")

def create_channel_keyboard(conf_id):
    data = confessions_db[conf_id]
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Add Comment", url=f"https://t.me/{BOT_USERNAME}?start=comm_{conf_id}")
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
    markup = create_channel_keyboard(conf_id)
    
    if data["photo_id"]:
        sent = await bot.send_photo(chat_id=CHANNEL_ID, photo=data["photo_id"], caption=channel_post_text, reply_markup=markup)
    else:
        sent = await bot.send_message(chat_id=CHANNEL_ID, text=channel_post_text, reply_markup=markup)
    
    data["channel_message_id"] = sent.message_id
    
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

# --- REPLIES INTERCEPTION FOR DISCUSSION GROUPS ---
@dp.message()
async def catch_discussion_forward(message: types.Message):
    """Intercepts and maps the channel's automatically forwarded post inside the discussion group"""
    try:
        # Check if this message was automatically forwarded from your channel
        if message.forward_from_chat and message.forward_from_chat.username == CHANNEL_ID.replace("@", ""):
            orig_msg_id = message.forward_from_message_id
            
            # Loop through our database to find the confession that matches this channel message ID
            for conf_id, data in confessions_db.items():
                if data.get("channel_message_id") == orig_msg_id:
                    data["discussion_chat_id"] = message.chat.id
                    data["discussion_message_id"] = message.message_id
                    logging.info(f"🎯 MATCHED: Confession #{conf_id} linked to Group Message ID {message.message_id}")
                    return
    except Exception as e:
        logging.error(f"Error mapping discussion chat forward: {e}")
        
        # Parse the dynamic confession ID out from the signature line
        first_line = text.split("\n")[0]
        conf_id = first_line.split("#")[-1].strip()
        
        if conf_id in confessions_db:
            confessions_db[conf_id]["discussion_chat_id"] = message.chat.id
            confessions_db[conf_id]["discussion_message_id"] = message.message_id
            logging.info(f"Successfully mapped confession #{conf_id} to group chat post {message.message_id}")
    except Exception as e:
        logging.error(f"Error mapping discussion chat forward: {e}")

# --- ANONYMOUS COMMENT SUBMISSION ROUTER ---
@dp.message(BotStates.waiting_for_comment, F.text)
async def process_anonymous_comment(message: types.Message, state: FSMContext):
    state_data = await state.get_data()
    conf_id = state_data.get("target_conf_id")
    data = confessions_db.get(conf_id)
    
    if not data or not data.get("discussion_message_id"):
        await message.answer("⚠️ System syncing! Please wait a moment for the post to register and try again.")
        await state.clear()
        return
    
    try:
        # Post the comment directly into the group as an explicit reply to the channel's copy
        await bot.send_message(
            chat_id=data["discussion_chat_id"],
            text=f"💬 **Anonymous:**\n\n{message.text}",
            reply_to_message_id=data["discussion_message_id"]
        )
        await message.answer("🚀 Your anonymous comment has been posted directly inside the replies!")
    except Exception as e:
        logging.error(f"Error dropping nested reply: {e}")
        await message.answer("❌ Failed to post comment inside the channel's native feed.")
        
    await state.clear()

# --- VOTING LOGIC ---
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
            
    await bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=data["channel_message_id"], reply_markup=create_channel_keyboard(conf_id))
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
            
    await bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=data["channel_message_id"], reply_markup=create_channel_keyboard(conf_id))
    await callback.answer()

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
