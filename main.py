import os
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from groq import Groq
import logging

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")

flask_app = Flask(__name__)
telegram_app = ApplicationBuilder().token(TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("البوت شغال ✅\nجرب /hmisc 1")

async def hmisc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم: /hmisc 1")
        return
    q_num = context.args[0]
    await update.message.reply_text(f"⏳ سؤال {q_num}...")
    try:
        client = Groq(api_key=GROQ_KEY)
        completion = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": f"اشرح السؤال رقم {q_num} من امتحان Hmisc للتمريض بالعربي مع الإجابة الصحيحة"}],
            max_tokens=2000
        )
        ans = completion.choices[0].message.content
        for i in range(0, len(ans), 4000):
            await update.message.reply_text(ans[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("hmisc", hmisc))

@flask_app.route('/')
def home():
    return "Bot Webhook Live ✅"

# هنا شلنا async
@flask_app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, telegram_app.bot)
        asyncio.run(telegram_app.process_update(update))
    except Exception as e:
        print(f"Webhook error: {e}")
    return "ok", 200

@flask_app.route('/setwebhook')
def setwebhook():
    try:
        url = f"{request.host_url}{TOKEN}"
        # نشيل / الاخيرة
        url = url.rstrip('/')
        asyncio.run(telegram_app.bot.set_webhook(url))
        return f"Webhook set to {url} ✅"
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)
