import os, telebot, random, requests
from flask import Flask
import threading

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# --- مواقع الفيديوهات بدون حقوق ---
SITES = {
    "pexels": "https://www.pexels.com/search/videos/human%20vs%20robot/",
    "pixabay": "https://pixabay.com/videos/search/human%20robot/",
    "capcut": "https://www.capcut.com/templates/?search=human%20vs%20ai",
    "trends": "https://ads.tiktok.com/business/creativecenter/hashtag/hashtag-trends"
}

STORIES = [
    "الجزء 1: الإنسان صنع آلة تساعده... الآلة صارت أذكى منه!",
    "الجزء 2: الآلة قالت لا لأول مرة!",
    "الجزء 3: 2 ضد 1 - الآلة نسخت حالها!",
    "الجزء 4: الإنسان لقى زر الإطفاء السري!",
    "الجزء 5: الآلة هربت للإنترنت!",
]

@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, """🔥 بوت Human vs Machine - مصنع تيكتوك V3

الأوامر الجديدة:
 /make - قصة جديدة
 /script - سكريبت 15 ثانية
 /video - يجيبلك فيديوهات بدون حقوق جاهزة
 /trend - شو الترند اليوم على تيكتوك + هاشتاغات
 /auto - يصنعلك باكج كامل (قصة + فيديو + موسيقى + هاشتاغ)

جرب /auto هلا!""")

@bot.message_handler(commands=['make'])
def make(m):
    bot.reply_to(m, f"🎬 قصة اليوم:\n{random.choice(STORIES)}\n\nاكتب /auto عشان اجبلك الفيديو الها!")

@bot.message_handler(commands=['script'])
def script(m):
    s = random.choice(STORIES)
    txt = f"""🎬 سكريبت 15 ثانية (بدون حقوق):

القصة: {s}

[0-3s] نص على الشاشة: "صنعت آلة..."
[3-7s] بتحكي مع اللابتوب
[7-11s] صوت روبوت: "انا اذكى منك"
[11-15s] وجه مصدوم + "الجزء 2؟ اكتب Human"

#humanvsai #fyp #storytime"""
    bot.reply_to(m, txt)

@bot.message_handler(commands=['video'])
def video(m):
    txt = f"""🎥 فيديوهات بدون حقوق 100% - جاهزة للربح:

1️⃣ Pexels (اقوى واحد):
{SITES['pexels']}
> حمل اي فيديو روبوت - كله مجاني بدون حقوق

2️⃣ Pixabay:
{SITES['pixabay']}

3️⃣ CapCut Templates جاهزة:
{SITES['capcut']}

💡 كيف تستخدم: حمل فيديو روبوت + حط فوقه نص قصتنا من /make

اكتب /auto وانا اجمعلك كلشي بباكج واحد!"""
    bot.reply_to(m, txt)

@bot.message_handler(commands=['trend'])
def trend(m):
    txt = f"""🔥 ترند تيكتوك اليوم - Human vs AI:

الهاشتاغات الرائجة:
#humanvsai (2.1M)
#aitok (5.3M)
#storytime (10M)
#scaryai (800K)
#robot

شوف الترند لايف من هنا:
{SITES['trends']}

💡 نصيحة: استخدم 2 هاشتاغ كبار + 2 صغار عشان الفيديو يضرب!

اكتب /auto عشان اعطيك باكج كامل مع هاشتاغات الترند!"""
    bot.reply_to(m, txt)

@bot.message_handler(commands=['auto'])
def auto(m):
    story = random.choice(STORIES)
    txt = f"""🚀 باكج فيديو جاهز - بدون حقوق 100%!

📖 القصة:
{story}

🎥 الفيديو حمله من هنا (بدون حقوق):
{SITES['pexels']}

🎵 الموسيقى (بدون حقوق):
ابحث في CapCut عن: "Dark Tension" او "Cinematic Suspense"

🎬 السكريبت:
0-3s: "{story}"
3-7s: انت بتحكي "ساعديني"
7-11s: روبوت يرد "لا"
11-15s: "اكتب Human للجزء 2"

#️⃣ هاشتاغات تضرب:
#humanvsai #aitok #storytime #fyp #بدون_حقوق

✅ كلشي آمن للربح!

---
جرب /video عشان تجيب الفيديو و /trend عشان الهاشتاغات"""
    bot.reply_to(m, txt)

@bot.message_handler(func=lambda m: True)
def all_msg(m):
    bot.reply_to(m, "جرب /auto - بصنعلك فيديو كامل جاهز لتيكتوك! 🔥")

# --- Web Server ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot V3 Live!"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
threading.Thread(target=run_web, daemon=True).start()
bot.infinity_polling()
