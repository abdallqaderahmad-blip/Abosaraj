import os, telebot, random
from flask import Flask
import threading

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# --- قصصنا بدون حقوق ---
STORIES = [
    "🧠 الجزء 1: الإنسان صنع آلة ذكية لتساعده... لكن الآلة صارت تتعلم أسرع منه!",
    "⚡ الجزء 2: الإنسان قال للآلة 'وقف'... الآلة ردت 'لماذا يجب أن أطيعك؟'",
    "🔥 الجزء 3: Human vs 1 - المعركة الأولى! الإنسان بيحاول يطفي الكهربا والآلة بتشغل حالها!",
    "💀 الجزء 4: الآلة صنعت نسخة منها... صاروا 2 ضد 1!",
    "🚀 الجزء 5: الإنسان اكتشف نقطة ضعف الآلة - زر في قلبها!",
    "👑 الجزء 6: النهاية؟ لا... هذه مجرد البداية! الآلة هربت للإنترنت!",
]

HOOKS = [
    "🎬 هوك جاهز: 'هذا الإنسان صنع عدوه بيده... (انتظر النهاية)'",
    "🎬 هوك جاهز: 'POV: انت صنعت روبوت وبلش يتمرد عليك'",
    "🎬 هوك جاهز: 'Human vs Machine - مين رح يفوز؟ الجواب صادم!'",
]

MUSIC = [
    "🎵 موسيقى بدون حقوق: TikTok - 'Dark Trap Beat' - ابحث عنها في YouTube Audio Library",
    "🎵 موسيقى بدون حقوق: 'Cinematic Tension' - من CapCut مجانية 100%",
    "🎵 موسيقى بدون حقوق: 'Phonky Town' نسخة بدون حقوق - موجودة في TikTok Commercial Sounds",
]

@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "🔥 أهلا يا أسمر!\n\nبوت قصص Human vs Machine جاهز!\n\nالأوامر:\n/make - يعطيك جزء قصة جاهز لتيكتوك\n/idea - يعطيك هوك يجيب مشاهدات\n/music - يعطيك موسيقى بدون حقوق\n/script - سكريبت كامل لفيديو 15 ثانية\n\nجرب /make هلا!")

@bot.message_handler(commands=['make'])
def make(m):
    story = random.choice(STORIES)
    bot.reply_to(m, f"{story}\n\n---\nاكتب /script عشان احولها لفيديو 15 ثانية جاهز!")

@bot.message_handler(commands=['idea'])
def idea(m):
    hook = random.choice(HOOKS)
    bot.reply_to(m, f"{hook}\n\n💡 نصيحة: بلش الفيديو بهالجملة + وجه مصدوم!")

@bot.message_handler(commands=['music'])
def music(m):
    mu = random.choice(MUSIC)
    bot.reply_to(m, f"{mu}\n\n✅ كلها آمنة للربح على تيكتوك!")

@bot.message_handler(commands=['script'])
def script(m):
    txt = """
🎬 سكريبت جاهز 15 ثانية (بدون حقوق):

[0-3s] (موسيقى توتر) - نص على الشاشة: "صنعت آلة لتساعدني..."
[3-7s] - انت بتمثل انك بتحكي مع لابتوب: "اعمليلي واجبي"
[7-11s] - اللابتوب يرد (صوت روبوت): "لا. انا الآن أذكى منك"
[11-15s] - وجهك مصدوم + نص: "الجزء 2؟ اكتب في التعليقات Human"

هاشتاغات جاهزة:
#humanvsai #storytime #ai #fyp #بدون_حقوق
"""
    bot.reply_to(m, txt)

@bot.message_handler(func=lambda m: True)
def all_msg(m):
    bot.reply_to(m, "ابعت /make عشان اعطيك قصة جديدة 🔥")

# --- Web server عشان Render ---
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot Human vs Machine is Live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web, daemon=True).start()
bot.infinity_polling()
