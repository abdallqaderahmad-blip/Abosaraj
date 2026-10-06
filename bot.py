import os
import asyncio
import logging
import threading

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================================================
# SETTINGS
# =========================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is missing from Render Environment Variables")


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# FLASK SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj Bot is running!", 200


@app.route("/health")
def health():
    return "OK", 200


def run_web_server():
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 أهلاً بك في Abosaraj Bot\n\n"
        "البوت شغال بنجاح.\n"
        "أرسل لي فكرتك ونبني عليها الخطوة القادمة."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "الأوامر المتاحة:\n\n"
        "/start - تشغيل البوت\n"
        "/help - المساعدة"
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "الأمر غير معروف. استخدم /help"
    )


# =========================================================
# TELEGRAM BOT
# =========================================================

async def run_bot():

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    # Unknown commands
    application.add_handler(
        CommandHandler(None, unknown_command)
    )

    logger.info("Starting Telegram bot...")

    await application.initialize()
    await application.start()

    # Start polling
    await application.updater.start_polling(
        drop_pending_updates=True
    )

    logger.info("Telegram bot is running!")

    # Keep the asyncio loop alive
    try:
        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:
        logger.info("Bot cancellation received.")

    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


# =========================================================
# MAIN
# =========================================================

def main():

    # Start Flask in another thread
    web_thread = threading.Thread(
        target=run_web_server,
        daemon=True
    )

    web_thread.start()

    logger.info(
        f"Web server started on port {PORT}"
    )

    # Start Telegram bot
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
