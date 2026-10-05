import os, requests, threading, time
import telebot
from flask import Flask

print("BOT FILE LOADED")

TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot running"

@bot.message_handler(commands=["start"])
def start(m):
    bot.reply_to(m, "Working! Send /reel cat")

@bot.message_handler(commands=["reel"])
def reel_handler(m):
    print(f"Got: {m.text}")
    parts = m.text.split()
    topic = parts[1] if len(parts) > 1 else "cat"
    msg = bot.reply_to(m, f"Searching {topic}...")
    try:
        url = "https://pixabay.com/api/videos/"
        params = {"key": PIXABAY_KEY, "q": topic, "per_page": 3}
        data = requests.get(url, params=params, timeout=20).json()
        hits = data.get("hits", [])
        if not hits:
            bot.edit_message_text(f"No video for {topic}", m.chat.id, msg.message_id)
            return
        vurl = hits[0]["videos"]["medium"]["url"]
        print("Found video")
        bot.edit_message_text("Uploading...", m.chat.id, msg.message_id)
        bot.send_video(m.chat.id, vurl, caption=topic)
        bot.delete_message(m.chat.id, msg.message_id)
    except Exception as e:
        print(f"ERR {e}")
        bot.edit_message_text(f"Error {e}", m.chat.id, msg.message_id)

def run_bot():
    print("Polling start")
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
