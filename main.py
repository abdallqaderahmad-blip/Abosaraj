import os
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from groq import Groq

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

# سيرفر وهمي عشان Render
flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return "Bot is Running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا! البوت شغال ✅\nجرب /hmisc 1")

async def hmisc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم: /hmisc 1")
        return
    q_num = context.args[0]
    await update.message.reply_text(f"جاري تحضير سؤال {q_num}...")
    prompt = f"اشرح سؤال Hmisc رقم {q_num} طبياً بطريقة مبسطة مع الإجابة الصحيحة والشرح"
    try:
        completion = client.chat.completions.create(model="llama3-70b-8192", messages=[{"role": "user", "content": prompt}], temperature=0.3)
        answer = completion.choices[0].message.content
        for i in range(0, len(answer), 4000):
            await update.message.reply_text(answer[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hmisc", hmisc))
    print("Starting bot...")
    app.run_polling(drop_pending_updates=True)
