import os
import json
import random
import logging
import asyncio
import threading
import requests
import psycopg2
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))
RENDER_URL = os.getenv("RENDER_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --- База данных ---
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cards (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    file_id TEXT NOT NULL
                )
            """)
        conn.commit()
    logger.info("База данных инициализирована")

def load_cards():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, file_id FROM cards ORDER BY id")
            rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "file_id": r[2]} for r in rows]

def save_card(name, file_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO cards (name, file_id) VALUES (%s, %s)", (name, file_id))
        conn.commit()

def clear_cards():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cards")
        conn.commit()

# --- Flask ---
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running!", 200

# --- Автопинг ---
def keep_alive():
    import time
    while True:
        try:
            if RENDER_URL:
                requests.get(RENDER_URL, timeout=10)
        except Exception as e:
            logger.warning(f"Пинг не удался: {e}")
        time.sleep(300)

# --- Колода ---
deck = []

def shuffle_deck(cards):
    global deck
    indices = list(range(len(cards)))
    random.shuffle(indices)
    deck[:] = indices

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cards = load_cards()
    if not cards:
        await update.message.reply_text("👋 Колода пуста. Администратор загружает карты командой /upload")
        return
    shuffle_deck(cards)
    await update.message.reply_text(f"🃏 Колода готова — {len(cards)} карт.\nНапиши число от 1 до {len(cards)}, чтобы вытащить карту.")

async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только для администратора.")
        return
    context.user_data["uploading"] = True
    await update.message.reply_text("📤 Режим загрузки активен.\nОтправляй фото одно за одним с подписью — названием карты.\nКогда закончишь — /done")

async def upload_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только для администратора.")
        return
    if not context.user_data.get("uploading"):
        await update.message.reply_text("Сначала начни загрузку командой /upload")
        return
    context.user_data["uploading"] = False
    cards = load_cards()
    shuffle_deck(cards)
    await update.message.reply_text(f"✅ Загрузка завершена. Всего в колоде: {len(cards)} карт.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.user_data.get("uploading"):
        return
    photo = update.message.photo[-1]
    file_id = photo.file_id
    caption = update.message.caption or ""
    if not caption.strip():
        context.user_data["pending_photo"] = file_id
        await update.message.reply_text("📝 Напиши название этой карты следующим сообщением.")
        return
    save_card(caption.strip(), file_id)
    cards = load_cards()
    await update.message.reply_text(f"✅ Карта {len(cards)}: «{caption.strip()}» сохранена.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id == ADMIN_ID and context.user_data.get("uploading") and context.user_data.get("pending_photo"):
        file_id = context.user_data.pop("pending_photo")
        save_card(text, file_id)
        cards = load_cards()
        await update.message.reply_text(f"✅ Карта {len(cards)}: «{text}» сохранена.")
        return

    cards = load_cards()
    if not cards:
        await update.message.reply_text("Колода пуста.")
        return
    if not deck:
        shuffle_deck(cards)
    if not text.isdigit():
        await update.message.reply_text(f"Напиши число от 1 до {len(cards)}.")
        return
    n = int(text)
    if n < 1 or n > len(deck):
        await update.message.reply_text(f"Число должно быть от 1 до {len(deck)}.")
        return
    card = cards[deck[n - 1]]
    await update.message.reply_photo(
        photo=card["file_id"],
        caption=f"🃏 *{card['name']}*",
        parse_mode="Markdown"
    )
    shuffle_deck(cards)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cards = load_cards()
    if not cards:
        await update.message.reply_text("Колода пуста.")
        return
    await update.message.reply_text(f"🃏 В колоде {len(cards)} карт.\nНапиши число от 1 до {len(cards)}.")

async def reset_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только для администратора.")
        return
    clear_cards()
    global deck
    deck = []
    await update.message.reply_text("🗑 Колода очищена. Загружай заново командой /upload")

async def run_bot():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("upload", upload_start))
    app.add_handler(CommandHandler("done", upload_done))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset_cards))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Бот запущен")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

def main():
    if not TOKEN:
        logger.error("BOT_TOKEN не задан!")
        return
    init_db()
    threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False),
        daemon=True
    ).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    logger.info(f"Flask запущен на порту {PORT}")
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
