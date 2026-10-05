import os
import json
import threading
import requests
from flask import Flask
from google import genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI = os.getenv("GEMINI_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL

client = genai.Client(api_key=GEMINI)

flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return "Bot Live - Abosaraj", 200
def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا بك في بوت أبو سراج لقصص الأطفال! 🎉\nأرسل قصة وسأحولها لـ 8 مشاهد مصورة.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = update.message.text
    await update.message.reply_text(f"⏳ جاري تحليل قصتك لـ 8 مشاهد...")
    prompt = f"حلل هذه القصة لـ 8 مشاهد. كل مشهد برومبت انجليزي كرتوني للأطفال بنفس الشخصية. أرجع JSON فقط: [{{\"scene\":1,\"prompt\":\"...\"}}] القصة: {story}"
    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = response.text.replace("```json","").replace("```","").strip()
        scenes = json.loads(text)
        for s in scenes[:8]:
            await update.message.reply_text(f"🎨 رسم المشهد {s['scene']}/8...")
            result = subscribe("fal-ai/flux/dev", arguments={"prompt": s['prompt'] + ", children cartoon storybook, cute, vibrant, consistent character"})
            await update.message.reply_photo(photo=result['images'][0]['url'], caption=f"المشهد {s['scene']}")
        await update.message.reply_text("✅ خلصت!")
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    # هذا السطر بيحل مشكلة الـ Conflict نهائيا
    try:
        requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    except: pass
    print("Bot Started...")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)
