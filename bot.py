
import os
import json
import threading
import requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe
from groq import Groq

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL
groq_client = Groq(api_key=GROQ_KEY)

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return '<h1 style="text-align:center;padding:50px">Bot Live ✅</h1>'

async def start(update, context):
    await update.message.reply_text("أهلا! أرسل قصة 🎨")

def get_scenes(story):
    prompt = f'Return ONLY JSON array: [{{"scene":1,"prompt":"english cartoon prompt"}}] Story: {story}'
    chat = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}]
    )
    text = chat.choices[0].message.content
    s = text.find('[')
    e = text.rfind(']') + 1
    return json.loads(text[s:e])

async def handle_message(update, context):
    if update.message.text.startswith("/"):
        return
    await update.message.reply_text("⚡️ Groq يحلل قصتك...")
    try:
        scenes = get_scenes(update.message.text)
        for sc in scenes[:8]:
            await update.message.reply_text(f"🎨 رسم المشهد {sc['scene']}/8...")
            result = subscribe("fal-ai/flux/dev", arguments={"prompt": sc['prompt'] + ", cute cartoon storybook, vibrant"})
            await update.message.reply_photo(photo=result['images'][0]['url'], caption=f"المشهد {sc['scene']}")
        await update.message.reply_text("✅ خلصت!")
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

def run_bot():
    try:
        requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    except:
        pass
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
