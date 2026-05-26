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

# ================= 2. INITIALIZE GLOBAL OBJECTS =================
bot: Bot = None
dp = Dispatcher(storage=MemoryStorage())

# Crucial: Ensure this clean public channel name matches your channel's public link
CHANNEL_PUBLIC_NAME = "wku_confessions_official" 
CHANNEL_USERNAME = f"@{CHANNEL_PUBLIC_NAME}"
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
            cursor.execute("INSERT INTO identity_map VALUES (?, ?, ?
