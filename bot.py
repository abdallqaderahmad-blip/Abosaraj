import os
import json
import threading
import google.generativeai as genai
from fal_client import subscribe
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")

if not TELEGRAM_TOKEN: raise RuntimeError("TELEGRAM_TOKEN missing")
if not GEMINI_API_KEY: raise RuntimeError("GEMINI_API_KEY missing")
if not FAL_KEY: raise RuntimeError("FAL_KEY missing")

os.environ["FAL_KEY"] = FAL_KEY
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")
VIDEO_MODEL = "fal-ai/kling-video/o3/pro/text-to-video"

web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Bot Live with Gemini Free!", 200
def run_web(): web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 أهلاً بك! البوت هلا مجاني 100% - أرسل قصتك!")

def create_scenes(story):
    prompt = f"""
You are a professional cinematic AI video director.
Convert this story into exactly 8 cinematic video scenes.
Each scene 8-10 seconds. Keep same characters.
Return ONLY valid JSON without markdown.
Format: {{"title": "short title", "narration": "Arabic narration", "scenes": [{{"scene": 1, "duration": 10, "prompt": "cinematic English video prompt"}}]}}
Story: {story}
"""
    response = model.generate_content(prompt)
    text = response.text.replace("```json", "").replace("```", "").strip()
    return text

async def handle_story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_story = update.message.text
    if len(user_story) < 10:
        await update.message.reply_text("القصة قصيرة")
        return
    await update.message.reply_text("⏳ جاري تحليل قصتك مع Gemini المجاني... وتقسيمها لـ 8 مشاهد")
    try:
        result_text = create_scenes(user_story)
        data = json.loads(result_text)
        title = data.get("title", "قصتك")
        narration = data.get("narration", "")
        scenes = data.get("scenes", [])
        await update.message.reply_text(f"✅ {title}\n\n📖 {narration}\n\n🎬 جاري توليد {len(scenes)} مشهد...")

        for i, scene in enumerate(scenes, 1):
            p = scene.get("prompt", "")
            await update.message.reply_text(f"🎥 مشهد {i}/8 - {p[:50]}...")
            try:
                result = subscribe(VIDEO_MODEL, arguments={"prompt": p})
                video_url = result.get("video", {}).get("url") if isinstance(result.get("video"), dict) else result.get("url")
                if video_url:
                    await update.message.reply_video(video_url, caption=f"مشهد {i}")
            except Exception as e:
                await update.message.reply_text(f"⚠️ فشل مشهد {i}: {e}")
                continue
        await update.message.reply_text("🎉 خلصت كل المشاهد يا أبو سر
