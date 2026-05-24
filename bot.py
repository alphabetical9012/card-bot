import os
import json
import random
import logging
import asyncio
import threading
import requests
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CARDS_FILE = "cards.json"
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))
RENDER_URL = os.getenv("RENDER_URL", "")

# --- Flask ---
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running!", 200

# --- Автопинг ---
def keep_alive():
    while True:
        try:
            if RENDER_URL:
                requests.get(RENDER_URL, timeout=10)
                logger.info("Пинг отправлен")
        except Exception as e:
            logger.warning(f"Пинг не удался: {e}")
        import time
        time.sleep(300)  # каждые 5 минут

# --- Карты ---
def load_cards():
    if os.path.exists(CARDS_FILE):
        with open(CARDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_cards(cards):
    with open(CARDS_FILE, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)

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
    context.user_data["upload_buffer"] = []
    await update.message.reply_text("📤 Режим загрузки активен.\nОтправляй фото одно за одним.\nКогда закончишь — /done")

async def upload_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только для администратора.")
        return
    if not context.user_data.get("uploading"):
        await update.message.reply_text("Сначала начни загрузку командой /upload")
        return
    buffer = context.user_data.get("upload_buffer", [])
    if not buffer:
        await update.message.reply_text("Ты не загрузила ни одной карты.")
        return
    # Дозагружаем к уже существующим
    existing = load_cards()
    existing.extend(buffer)
    save_cards(existing)
    shuffle_deck(existing)
    context.user_data["uploading"] = False
    context.user_data["upload_buffer"] = []
    await update.message.reply_text(f"✅ Сохранено {len(buffer)} карт. Всего в колоде: {len(existing)}.")

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
    buffer = context.user_data.setdefault("upload_buffer", [])
    card_num = len(load_cards()) + len(buffer) + 1
    buffer.append({"id": card_num, "name": caption.strip(), "file_id": file_id})
    await update.message.reply_text(f"✅ Карта {card_num}: «{caption.strip()}» сохранена.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id == ADMIN_ID and context.user_data.get("uploading") and context.user_data.get("pending_photo"):
        file_id = context.user_data.pop("pending_photo")
        buffer = context.user_data.setdefault("upload_buffer", [])
        card_num = len(load_cards()) + len(buffer) + 1
        buffer.append({"id": card_num, "name": text, "file_id": file_id})
        await update.message.reply_text(f"✅ Карта {card_num}: «{text}» сохранена.")
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
    save_cards([])
    global deck
    deck = []
    await update.message.reply_text("🗑 Колода очищена. Можно загружать заново командой /upload")

# --- Основная async функция ---
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

# --- Запуск ---
def main():
    if not TOKEN:
        logger.error("BOT_TOKEN не задан!")
        return

    threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False),
        daemon=True
    ).start()

    threading.Thread(target=keep_alive, daemon=True).start()

    logger.info(f"Flask запущен на порту {PORT}")
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
