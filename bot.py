import os, requests, threading, time, random
import telebot
from flask import Flask

print("BOT FILE LOADED - TIKTOK MODE FIXED")
TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def home():
    return "TikTok Bot Running"

@bot.message_handler(commands=["start"])
def start(m):
    bot.reply_to(m, "TikTok Ready! Use /reel dance")

@bot.message_handler(commands=["reel"])
def reel_handler(m):
    txt = m.text.split(maxsplit=1)
    topic = txt[1] if len(txt) > 1 else "cat"
    msg = bot.reply_to(m, "Searching TikTok: " + topic)
    try:
        url = "https://www.tikwm.com/api/feed/search"
        payload = {"keywords": topic, "count": 10, "HD": 1}
        hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.tikwm.com/"}
        r = requests.post(url, data=payload, headers=hdr, timeout=25)
        vids = r.json().get("data", {}).get("videos", [])
        if not vids:
            r2 = requests.post("https://www.tikwm.com/api/feed/list", data={"count":10}, headers=hdr, timeout=25)
            vids = r2.json().get("data", {}).get("videos", [])
        if not vids:
            bot.edit_message_text("No result for " + topic, m.chat.id, msg.message_id)
            return
        v = random.choice(vids)
        vurl = v.get("play") or v.get("hdplay") or v.get("wmplay")
        ttl = v.get("title", "")[:120]
        bot.edit_message_text("Found! Uploading...", m.chat.id, msg.message_id)
        bot.send_video(m.chat.id, vurl, caption=ttl + " #" + topic + " #fyp")
        bot.delete_message(m.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text("Error: " + str(e), m.chat.id, msg.message_id)

@bot.message_handler(func=lambda m: "tiktok.com" in m.text)
def tlink(m):
    msg = bot.reply_to(m, "Downloading TikTok...")
    try:
        r = requests.post("https://www.tikwm.com/api/", data={"url": m.text, "HD":1}, headers={"User-Agent":"Mozilla/5.0"}, timeout=25)
        d = r.json().get("data", {})
        vurl = d.get("play") or d.get("hdplay")
        ttl = d.get("title", "TikTok")[:120]
        if vurl:
            bot.send_video(m.chat.id, vurl, caption=ttl + " Ready!")
            bot.delete_message(m.chat.id, msg.message_id)
        else:
            bot.edit_message_text("Failed", m.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text("Error " + str(e), m.chat.id, msg.message_id)

def run_bot():
    print("Polling start")
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True)

threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
