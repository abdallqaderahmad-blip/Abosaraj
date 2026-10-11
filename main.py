import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from groq import Groq

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلا! البوت شغال ✅\n"
        "جرب:\n"
        "/hmisc 1\n"
        "/hmisc 2"
    )

async def hmisc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم: /hmisc 1")
        return

    q_num = context.args[0]
    await update.message.reply_text(f"جاري تحضير سؤال {q_num}...")

    prompt = f"اشرح سؤال Hmisc رقم {q_num} طبياً بطريقة مبسطة مع الإجابة الصحيحة والشرح"

    if not client:
        await update.message.reply_text("❌ GROQ_API_KEY ناقص في Render → Environment")
        return

    try:
        completion = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        answer = completion.choices[0].message.content
        # تليجرام الحد 4096 حرف
        for i in range(0, len(answer), 4000):
            await update.message.reply_text(answer[i:i+4000])
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text(f"خطأ: {e}")

if __name__ == "__main__":
    if not TOKEN:
        print("❌ BOT_TOKEN ناقص في Environment Variables!")
        exit(1)

    print("Starting bot...")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hmisc", hmisc))

    # هذا السطر هو اللي يحل مشكلة الـ Conflict
    app.run_polling(drop_pending_updates=True)
