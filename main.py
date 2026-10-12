import os
import logging
from flask import Flask
from threading import Thread
import time
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from groq import Groq

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")

if not TOKEN:
    print("❌ BOT_TOKEN مش موجود في Environment Variables")
if not GROQ_KEY:
    print("❌ GROQ_API_KEY مش موجود في Environment Variables")

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is Live! ✅ - " + str(time.time())

@flask_app.route('/health')
def health():
    return "OK", 200

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "البوت شغال تمام ✅\n\n"
        "الأوامر:\n"
        "/hmisc 1 - سؤال Hmisc رقم 1\n"
        "/hmisc 2 - سؤال Hmisc رقم 2\n"
        "وهكذا..."
    )

async def hmisc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ استخدم: /hmisc 1\nمثال: /hmisc 5")
        return

    q_num = context.args[0]
    await update.message.reply_text(f"⏳ جاري تحضير سؤال {q_num}...")

    try:
        client = Groq(api_key=GROQ_KEY)
        prompt = f"أنت مدرس تمريض خبير. اشرح السؤال رقم {q_num} من امتحان Hmisc مع ذكر الإجابة الصحيحة والشرح الطبي المبسط بالعربي."

        completion = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000
        )
        answer = completion.choices[0].message.content
        # تيليجرام اخره 4096 حرف
        for i in range(0, len(answer), 4000):
            await update.message.reply_text(answer[i:i+4000])

    except Exception as e:
        logging.error(f"Error in hmisc: {e}")
        await update.message.reply_text(f"❌ حصل خطأ: {e}")

def run_bot():
    print("🚀 Starting Telegram Bot...")
    try:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("hmisc", hmisc))
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
    except Exception as e:
        print(f"❌ Bot crashed: {e}")
        # اعادة تشغيل بعد 5 ثواني
        time.sleep(5)
        run_bot()

if __name__ == "__main__":
    # شغل البوت في Thread منفصل
    bot_thread = Thread(target=run_bot, daemon=False)
    bot_thread.start()

    # شغل Flask في الرئيسي عشان Render يشوفه
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 Flask running on port {port}")
    flask_app.run(host='0.0.0.0', port=port, debug=False)
