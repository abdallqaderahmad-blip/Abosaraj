import os, requests, threading, telebot
from flask import Flask
import time

TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def home(): return "OK"

@bot.message_handler(commands=["start"])
def start(m): bot.reply_to(m, "✅ شغال! ابعت /reel cat")

@bot.message_handler(commands=["reel"])
def reel(m):
    topic = m.text.replace("/reel","").strip() or "cat"
    # منع التكرار
    if hasattr(reel, 'busy') and reel.busy: return
    reel.busy = True

    status = bot.reply_to(m, f"⏳ بدور على {topic}... ثانية وحدة")
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={
            "key": PIXABAY_KEY, "q": topic, "per_page": 3
        }, timeout=20).json()

        if not r.get("hits"):
            bot.edit_message_text(f"❌ ما لقيت {topic} جرب: dog, nature", m.chat.id, status.message_id)
            reel.busy = False
            return

        vurl = r["hits"][0]["videos"]["medium"]["url"]
        bot.edit_message_text(f"✅ لقيته! بحمل...", m.chat.id, status.message_id)
        bot.send_video(m.chat.id, vurl, caption=f"جاهز للريلز: {topic} ✅")
        bot.delete_message(m.chat.id, status.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}\nتأكد من PIXABAY_KEY", m.chat.id, status.message_id)
        print(e)
    reel.busy = False

def run():
    bot.remove_webhook()
    time.sleep(3)
    bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)

if __name__ == "__main__":
    threading.Thread(target=run, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
