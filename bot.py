import os
import asyncio
import threading
import logging

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "ERROR: TELEGRAM_TOKEN is not set in Render Environment Variables."
    )


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("Abosaraj")


# =========================================================
# FLASK WEB SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj Bot is running!", 200


@app.route("/health")
def health():
    return "OK", 200


def run_flask():
    logger.info(f"Starting web server on port {PORT}")

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

    if update.message:
        await update.message.reply_text(
            "🔥 أهلاً بك في Abosaraj Bot!\n\n"
            "البوت يعمل الآن بنجاح.\n\n"
            "استخدم /help لمعرفة الأوامر."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.message:
        await update.message.reply_text(
            "🤖 أوامر Abosaraj:\n\n"
            "/start - تشغيل البوت\n"
            "/help - المساعدة\n\n"
            "🚧 نظام صناعة الفيديو سيتم تركيبه هنا."
        )


# =========================================================
# TELEGRAM BOT
# =========================================================

async def run_bot():

    logger.info("Creating Telegram application...")

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    # -------------------------
    # COMMANDS
    # -------------------------

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    # -------------------------
    # START BOT
    # -------------------------

    logger.info("Initializing Telegram bot...")

    await application.initialize()

    logger.info("Starting Telegram bot...")

    await application.start()

    logger.info("Starting Telegram polling...")

    await application.updater.start_polling(
        drop_pending_updates=True
    )

    logger.info("===================================")
    logger.info("      ABOSARAJ BOT IS ONLINE")
    logger.info("===================================")

    # Keep bot alive
    try:

        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:

        logger.info("Bot cancellation received.")

    finally:

        logger.info("Stopping Telegram polling...")

        await application.updater.stop()

        logger.info("Stopping Telegram application...")

        await application.stop()

        logger.info("Shutting down Telegram application...")

        await application.shutdown()


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info("===================================")
    logger.info("        STARTING ABOSARAJ")
    logger.info("===================================")

    # -----------------------------------------------------
    # Start Flask in background thread
    # -----------------------------------------------------

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
    )

    flask_thread.start()

    logger.info(
        f"Flask web server started on port {PORT}"
    )

    # -----------------------------------------------------
    # Start Telegram bot
    # -----------------------------------------------------

    try:

        asyncio.run(run_bot())

    except KeyboardInterrupt:

        logger.info("Abosaraj stopped.")

    except Exception as e:

        logger.exception(
            f"Fatal bot error: {e}"
        )

        raise


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()
