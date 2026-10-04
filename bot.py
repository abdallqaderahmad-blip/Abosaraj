import os, telebot, random, textwrap
from PIL import Image, ImageDraw, ImageFont
from flask import Flask
import threading

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

def make_story_image(text, filename="/tmp/story.jpg"):
    # خلفية سينمائية
    img = Image.new('RGB', (1080, 1920), color=(10,10,20))
    draw = ImageDraw.Draw(img)
    # مستطيل علوي للنص
    draw.rectangle([(0,0),(1080,300)], fill=(0,0,0))
    # حاول خط كبير
    try:
        font = ImageFont.truetype("arial.ttf", 60)
    except:
        font = ImageFont.load_default()
    
    wrapped = "\n".join(textwrap.wrap(text, width=20))
    draw.text((50,50), wrapped, fill=(255,255,255), font=font, spacing=10)
    draw.text((50, 1600), "Human vs Machine - Part X #fyp #humanvsai", fill=(200,200,200), font=font)
    img.save(filename)
    return filename

@bot.message_handler(commands=['start','help'])
def start(m):
    bot.reply_to(m, "🔥 V4-Light شغال!\n\nاكتب:\n/make النص تبعك\nمثال: /make الآلة نسخت حالها وصاروا 2 ضدي!\n\nورح اصنعلك صورة فيديو جاهزة 9:16")

@bot.message_handler(commands=['make'])
def make(m):
    txt = m.text.replace("/make","").strip()
    if not txt:
        txt = random.choice(["الآلة هربت للإنترنت!","الإنسان لقى زر الإطفاء السري!","2 ضد 1 - الآلة نسخت حالها!"])
    
    bot.send_message(m.chat.id, f"⏳ بصنع فيديو من: {txt}")
    path = make_story_image(txt)
    with open(path, 'rb') as photo:
        bot.send_photo(m.chat.id, photo, caption=f"🎬 جاهز!\n{txt}\n\nنزله وحطه في CapCut > Add Music > Export")
    # كمان ابعته كـ ملف فيديو وهمي (صورة)
    
@bot.message_handler(commands=['auto'])
def auto(m):
    story = random.choice(["الجزء 3: 2 ضد 1 - الآلة نسخت حالها!","الجزء 4: زر الإطفاء السري!","الجزء 5: الآلة هربت!"])
    path = make_story_image(story)
    with open(path, 'rb') as photo:
        bot.send_photo(m.chat.id, photo, caption=f"🚀 باكج: {story}")

app = Flask(__name__)
@app.route('/')
def home(): return "Bot V4-Light Live - Makes video from text!"
def run_web():
    port=int(os.environ.get("PORT",10000))
    app.run(host='0.0.0.0',port=port)
threading.Thread(target=run_web,daemon=True).start()
bot.infinity_polling()
