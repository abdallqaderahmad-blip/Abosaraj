import os
import json
import threading
import asyncio
from flask import Flask
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from fal_client import subscribe

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI = os.getenv("GEMINI_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL

genai.configure(api_key=GEMINI)
model = genai.GenerativeModel("gemini-1.5-flash")

flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return "Bot is Live", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً! ابعت القصة 🎬")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = update.message.text
    await update.message.reply_text("⏳ بحلل القصة...")
    try:
        prompt = f'Convert to JSON only: {{"title":"x","scenes":[{{"prompt":"english cinematic video prompt"}}]}} Story:{story}'
        resp = model.generate_content(prompt)
        txt = resp.text.replace("```json","").replace("```","").strip()
        data = json.loads(txt)
        for i, sc in enumerate(data.get("scenes",[])[:8], 1):
            await update.message.reply_text(f"🎥 مشهد {i}")
            r = subscribe("fal-ai/kling-video/o3/pro/text-to-video", arguments={"prompt": sc["prompt"]})
            video = r.get("video",{})
            url = video.get("url") if isinstance(video, dict) else r.get("url")
            if url:
                await update.message.reply_video(url, caption=f"مشهد {i}")
        await update.message.reply_text("✅ خلصت!")
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot Started...")
    loop.run_until_complete(app.run_polling(close_loop=False))

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    run_bot()
