import os, requests, threading, time, random
import telebot
from flask import Flask

print("BOT FILE LOADED - TIKTOK MODE")

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def home():
    return "TikTok Bot Running"

@bot.message_handler(commands=["start"])
def start(m):
    bot.reply_to(m, "🔥 بوت التيكتوك جاهز!\n\nاكتب:\n/reel قطط\n/reel رقص\n/reel سيارات\n\nورح اجيبلك فيديو تيكتوك حقيقي بصوت وطويل جاهز للتحميل!")

@bot.message_handler(commands=["reel"])
def reel_handler(m):
    parts = m.text.split(maxsplit=1)
    topic = parts[1] if len(parts) > 1 else "cat"
    msg = bot.reply_to(m, f"🔍 بدور على تيكتوك ترند عن: {topic}...")

    try:
        # نستخدم TikWM - يجيب تيكتوك حقيقي بصوت
        url = "https://www.tikwm.com/api/feed/search"
        payload = {"keywords": topic, "count": 10, "cursor": 0, "HD": 1}

        r = requests.post(url, data=payload, timeout=20, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.tikwm.com/"
        })
        data = r.json()

        videos = data.get("data", {}).get("videos", [])
        if not videos:
            # لو ما لقى، جرب يجيب فيديو عام ترند
            url2 = "https://www.tikwm.com/api/feed/list"
            r2 = requests.post(url2, data={"count": 10}, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            videos = r2.json().get("data", {}).get("videos", [])

        if not videos:
            bot.edit_message_text(f"ما لقيت عن {topic} جرب كلمة ثانية", m.chat.id, msg.message_id)
            return

        video = random.choice(videos)
        video_url = video.get("play") or video.get("wmplay") or video.get("hdplay")
        title = video.get("title", topic)
        author = video.get("author", {}).get("unique_id", "tiktok")

        bot.edit_message_text(f"✅ لقيته! بحمله هلا... بصوت كامل", m.chat.id, msg.message_id)

        # ارسله كفيديو جاهز للتيكتوك
        caption = f"🎬 {title[:100]}\n\n👤 @{author}\n\n# {topic} #fyp #viral #foryou\nجاهز للتحميل على تيكتوك 👇"
        bot.send_video(m.chat.id, video_url, caption=caption)
        bot.delete_message(m.chat.id, msg.message_id)
        print(f"Sent TikTok: {topic}")

    except Exception as e:
        print(f"ERR {e}")
        bot.edit_message_text(f"Error: {e}\nجرب كلمة ثانية", m.chat.id, msg.message_id)

# لو بعت رابط تيكتوك مباشر
@bot.message_handler(func=lambda m: "tiktok.com" in m.text)
def tiktok_link(m):
    msg = bot.reply_to(m, "🔗 شفت رابط تيكتوك، بنزله بدون علامة...")
    try:
        r = requests.post("https://www.tikwm.com/api/", data={"url": m.text, "HD": 1}, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json().get("data", {})
        vurl = data.get("play") or data.get("hdplay")
        title = data.get("title", "TikTok Video")
        if vurl:
            bot.send_video(m.chat.id, vurl, caption=f"✅ {title[:150]}\n\nجاهز للرفع على تيكتوك بدون علامة!")
            bot.delete_message(m.chat.id, msg.message_id)
        else:
            bot.edit_message_text("ما قدرت انزله، جرب رابط ثاني", m.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"Error {
