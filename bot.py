import os, threading, time, random, yt_dlp, requests
import telebot
from flask import Flask

print("YT-DLP MODE")
TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)
@app.route('/')
def home(): return "OK YTDLP"

@bot.message_handler(commands=["start"])
def start(m):
    bot.reply_to(m, "🔥 Fixed! Send TikTok link OR use /reel dance")

def get_direct_url(tiktok_url):
    ydl_opts = {'quiet':True,'no_warnings':True,'format':'mp4'}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(tiktok_url, download=False)
        return info.get('url'), info.get('title')

@bot.message_handler(commands=["reel"])
def reel(m):
    topic = m.text.split(maxsplit=1)[1] if len(m.text.split())>1 else "dance"
    wait = bot.reply_to(m, f"🔍 Searching '{topic}' on TikTok...")
    try:
        ydl_opts = {'quiet':True,'no_warnings':True,'format':'mp4','playlistend':5}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # نبحث بصفحة الهاشتاق
            info = ydl.extract_info(f"https://www.tiktok.com/tag/{topic}", download=False)
            entries = [e for e in info.get('entries',[]) if e]
            if not entries:
                # جرب سيرش عام
                info = ydl.extract_info(f"ytsearch5:tiktok {topic}", download=False)
                entries = info.get('entries',[])
            if entries:
                vid = random.choice(entries)
                vurl = vid.get('url') or vid.get('webpage_url')
                direct, title = get_direct_url(vurl) if 'tiktok.com' in vurl else (vid.get('url'), vid.get('title'))
                if direct:
                    bot.send_video(m.chat.id, direct, caption=(title or topic)[:200] + f"\n#{topic} #fyp")
                    bot.delete_message(m.chat.id, wait.message_id)
                    return
        bot.edit_message_text("Not found, try another word or send link", m.chat.id, wait.message_id)
    except Exception as e:
        bot.edit_message_text(f"Error: {str(e)[:200]}", m.chat.id, wait.message_id)

@bot.message_handler(func=lambda m: "tiktok.com" in m.text)
def link_handler(m):
    wait = bot.reply_to(m, "⬇️ Downloading with sound...")
    try:
        url, title = get_direct_url(m.text.strip())
        if url:
            bot.send_video(m.chat.id, url, caption=(title or "")[:200] + "\nReady for Reels ✅")
            bot.delete_message(m.chat.id, wait.message_id)
        else:
            bot.edit_message_text("Failed", m.chat.id, wait.message_id)
    except Exception as e:
        bot.edit_message_text(f"Error: {str(e)[:200]}", m.chat.id, wait.message_id)

def run_bot():
    bot.remove_webhook()
    time.sleep(2)
    bot.infinity_polling(skip_pending=True)

threading.Thread(target=run_bot, daemon=True).start()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)))
