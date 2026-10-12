import os
import logging
from flask import Flask
from threading import Thread
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from groq import Groq

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")

flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return "Bot is Running! ✅"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("البوت شغال ✅ جرب /hmisc 1")

async def hmisc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم: /hmisc 1")
        return
    q = context.args[0]
    await update.message.reply_text(f"جاري تحضير سؤال {q}...")
    try:
        client = Groq(api_key=GROQ_KEY)
        comp = client.chat.completions.create(model="llama3-70b-8192", messages=[{"role":"user","content":f"اشرح سؤال Hmisc رقم {q} طبيا مع الاجابة"}])
        await update.message.reply_text(comp.choices[0].message.content[:4000])
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

def run_bot():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hmisc", hmisc))
    print("Starting bot...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)
