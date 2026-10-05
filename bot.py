import os, json, threading
import google.generativeai as genai
from fal_client import subscribe
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL_KEY
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

web_app = Flask(__name__)
@web_app.route('/')
def home(): return "OK", 200
def run_web(): web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

async def start(update, context):
    await update.message.reply_text("Bot Ready! Send story")

def make_scenes(story):
    p = f"You are director. Convert to 8 scenes JSON only. Format: {{\"title\":\"t\",\"scenes\":[{{\"prompt\":\"English prompt\"}}]}} Story: {story}"
    r = model.generate_content(p)
    t = r.text.replace("```json","").replace("```","").strip()
    return t

async def handle(update, context):
    txt = update.message.text
    await update.message.reply_text("Analyzing...")
    try:
        raw = make_scenes(txt)
        data = json.loads(raw)
        scenes = data.get("scenes", [])
        await update.message.reply_text(f"Generating {len(scenes)} scenes")
        for i, s in enumerate(scenes, 1):
            pr = s.get("prompt","")
            await update.message.reply_text(f"Scene {i}")
            res = subscribe("fal-ai/kling-video/o3/pro/text-to-video", arguments={"prompt": pr})
            url = res.get("video",{}).get("url") if isinstance(res.get("video"), dict) else res.get("url")
            if url:
                await update.message.reply_video(url, caption=f"Scene {i}")
        await update.message.reply_text("Done!")
    except Exception as e:
        await update.message.reply_text(f"Error {e}")

def build():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    return app

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    build().run_polling()
