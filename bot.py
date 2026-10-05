import os, requests, threading, time
import telebot
from flask import Flask

print("=== BOT FILE LOADED ===")

TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")

print(f"BOT_TOKEN exists: {bool(TOKEN)}")
print(f"PIXABAY_KEY exists: {bool(PIXABAY_KEY)}")

if not TOKEN or not PIXABAY_KEY:
    print("ERROR: Missing keys in Environment!")
else:
    print("Keys OK")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running! Send /reel cat in Telegram"

@bot.message_handler(commands=["start"])
def start(m):
    bot.reply_to(m, "✅ شغال تمام!\nابعت /reel cat\nاو /reel dog")

@bot.message_handler(commands=["reel"])
def reel_handler(m):
    print(f"Got /reel from {m.chat.id}")
    topic = m.text.replace("/reel","").strip() or "cat"

    msg = bot.reply_to(m, f"⏳ بدور على {topic}...")
    try:
        url = "https://pixabay.com/api/videos/"
        params = {"key": PIXABAY_KEY, "q": topic, "per_page": 3}
        print(f"Searching Pixabay for {topic}")
        r = requests.get(url, params=params, timeout=20).json()

        hits = r.get("hits", [])
        if not hits:
            bot.edit_message_text(f"❌ ما لقيت {topic}", m.chat.id, msg.message_id)
            return

        video_url = hits[0]["videos"]["medium"]["url"]
        print(f"Found video: {video_url[:50]}")

        bot.edit_message_text("✅ لقيته، بحمل...", m.chat.id, msg.message_id)
        bot.send_video(m.chat.id, video_url, caption=f"جاهز للريلز: {topic}
