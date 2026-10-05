import os, json, threading, requests, time
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe

print("Starting Bot...")

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
FAL = os.getenv("FAL_KEY")

if not TOKEN: print("❌ TELEGRAM_TOKEN ناقص")
if not GROQ_KEY: print("❌ GROQ_API_KEY ناقص")
if not FAL: print("❌ FAL_KEY ناقص")

os.environ["FAL_KEY"] = FAL

try:
    from groq import Groq
    groq_client = Groq(api_key=GROQ_KEY)
    print("✅ Groq client ready")
except Exception as e:
    print(f"❌ Groq init failed: {e}")
    groq_client = None

flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return '<h1>Bot Live ✅ - Check logs for errors</h1>'
def run_flask():
    print("Flask starting on port 10000")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا! أرسل قصة 🎨")

def get_scenes(story):
    prompt = f'Return ONLY JSON array: [{{"scene":1,"prompt":"english cartoon prompt"}}] Story: {story}'
    chat = groq
