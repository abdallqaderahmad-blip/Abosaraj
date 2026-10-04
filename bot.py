import os, telebot, random
from flask import Flask
import threading
TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
SITES = {
"pexels": "https://www.pexels.com/search/videos/human%20vs%20robot/",
"pixabay": "https://pixabay.com/videos/search/human%20robot/",
"capcut": "https://www.capcut.com/templates/?search=human%20vs%20ai",
"trends": "https://ads.tiktok.com/business/creativecenter/hashtag/hashtag-trends"
}
STORIES = ["الجزء 1: الإنسان صنع آلة تساعده... الآلة صارت أذكى منه!","الجزء 2: الآلة قالت لا لأول مرة!","الجزء 3: 2 ضد 1 - الآلة نسخت حالها!","الجزء 4: الإنسان لقى زر الإطفاء السري!","الجزء 5: الآلة هربت للإنترنت!"]
@bot.message_handler(commands=['start','help'])
def start(m):
    bot.reply_to(m,"🔥 بوت Human vs Machine V3\n/auto - باكج كامل\n/video - فيديوهات بدون حقوق\n/trend - ترند تيكتوك\n/make - قصة جديدة")
@bot.message_handler(commands=['auto'])
def auto(m):
    s=random.choice(STORIES)
    bot.reply_to(m,f"🚀 باكج فيديو جاهز:\n\n📖 القصة: {s}\n\n🎥 الفيديو: {SITES['pexels']}\n🎵 موسيقى: Dark Tension في CapCut\n\n#humanvsai #fyp")
@bot.message_handler(commands=['video'])
def video(m):
    bot.reply_to(m,f"🎥 حمل فيديو بدون حقوق:\n{SITES['pexels']}\n\n{SITES['pixabay']}")
@bot.message_handler(commands=['trend'])
def trend(m):
    bot.reply_to(m,f"🔥 ترند اليوم:\n#humanvsai #aitok #storytime #fyp\n{SITES['trends']}")
@bot.message_handler(commands=['make'])
def make(m):
    bot.reply_to(m,random.choice(STORIES))
@bot.message_handler(func=lambda x: True)
def allmsg(m):
    t=m.text.lower()
    if "auto" in t: return auto(m)
    if "video" in t: return video(m)
    if "trend" in t: return trend(m)
    bot.reply_to(m,"جرب /auto")
app=Flask(__name__)
@app.route('/')
def home(): return "Bot V3 Live!"
def run_web():
    port=int(os.environ.get("PORT",10000))
    app.run(host='0.0.0.0',port=port)
threading.Thread(target=run_web,daemon=True).start()
bot.infinity_polling()
