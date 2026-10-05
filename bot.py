import os, requests, threading, time, random
import telebot
from flask import Flask

print("TIKTOK V3 FIX")
TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def home():
    return "OK V3"

@bot.message_handler(commands=["start"])
def start(m):
    bot.reply_to(m, "Ready! /reel dance")

def get_tiktok_by_keyword(topic):
    # جرب 3 APIs مختلفة
    headers = {"User-Agent": "Mozilla/5.0"}
    # 1- TikWM
    try:
        r = requests.post("https://www.tikwm.com/api/feed/search", data={"keywords":topic,"count":10,"HD":1}, headers=headers, timeout=15)
        if r.text.strip().startswith("{"):
            vids = r.json().get("data",{}).get("videos",[])
            if vids: return random.choice(vids)
    except: pass
    # 2- Fallback API
    try:
        r = requests.get(f"https://api.tiklydown.eu.org/api/download?url=https://www.tiktok.com/tag/{topic}", headers=headers, timeout=15)
        # هذا بس للروابط، للكلمات نستخدم Pexels كبديل مؤقت بفيديو طويل بصوت
    except: pass
    return None

def get_pexels_fallback(topic):
    # فيديو طويل 20-30 ثانية بصوت اذا فشل التيكتوك
    try:
        PEXELS = "wRZ5R4Y0N8z8V8k8..."
        # استخدم Pixabay HD طويل
        r = requests.get(f"https://pixabay.com/api/videos/?key=47212371-3d5e2e3c1c8b9a5f8a5c9b5c2&q={topic}&per_page=10", timeout=15).json()
        hits = r.get("hits", [])
        if hits:
            v = random.choice(hits)
            return v["videos"]["large"]["url"], "Stock but long"
    except: pass
    return None, None

@bot.message_handler(commands=["reel"])
def reel(m):
    topic = m.text.split(maxsplit=1)[1] if len(m.text.split())>1 else "dance"
    msg = bot.reply_to(m, "🔍 Searching TikTok: " + topic)
    try:
        data = get_tiktok_by_keyword(topic)
        if data and data.get("play"):
            vurl = data.get("play") or data.get("hdplay")
            bot.send_video(m.chat.id, vurl, caption=data.get("title","")[:150] + " #" + topic)
            bot.delete_message(m.chat.id, msg.message_id)
            return

        # لو التيكتوك فشل، جيب فيديو طويل بديل
        bot.edit_message_text("TikTok blocked, trying backup...", m.chat.id, msg.message_id)
        # استخدم API ثاني مباشر
        r = requests.post("https://tikwm.com/api/feed/list", data={"count":10}, headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.tikwm.com/"}, timeout=20)
        if r.text.strip():
            j = r.json()
            vids = j.get("data",{}).get("videos",[])
            if vids:
                v = random.choice(vids)
                vurl = v.get("play") or v.get("hdplay")
                bot.send_video(m.chat.id, vurl, caption=v.get("title","")[:150] + f" #{topic} #fyp")
                bot.delete_message(m.chat.id, msg.message_id)
                return

        bot.edit_message_text("API blocked from Render. Need proxy.", m.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text("Error: " + str(e)[:200], m.chat.id, msg.message_id)

@bot.message_handler(func=lambda m: "tiktok.com" in m.text)
def link(m):
    msg = bot.reply_to(m, "Downloading link...")
    try:
        for api_url in ["https://www.tikwm.com/api/", "https://tikwm.com/api/"]:
            try:
                r = requests.post(api_url, data={"url": m.text, "HD":1}, headers={"User-Agent":"Mozilla/5.0"}, timeout=20)
                if r.text.strip().startswith("{"):
                    d = r.json().get("data",{})
                    vurl = d.get("play") or d.get("hdplay")
                    if vurl:
                        bot.send_video(m.chat.id, vurl, caption=d.get("title","")[:150] + " Ready for TikTok")
                        bot.delete_message(m.chat.id, msg.message_id)
                        return
            except: continue
        bot.edit_message_text("Failed to download, TikTok blocking Render IP", m.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text("Error " + str(e), m.chat.id, msg.message_id)

def run_bot():
    bot.remove_webhook()
    time.sleep(2)
    bot.infinity_polling(skip_pending=True)

threading.Thread(target=run_bot, daemon=True).start()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
