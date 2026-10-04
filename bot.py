import os, telebot, textwrap, random
from PIL import Image, ImageDraw, ImageFont
import imageio
from flask import Flask
import threading

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

def make_video_from_text(text, out="/tmp/story.mp4"):
    W, H = 1080, 1920
    frames = []
    try:
        font_big = ImageFont.truetype("DejaVuSans.ttf", 80)
    except:
        font_big = ImageFont.load_default()

    lines = textwrap.wrap(text, width=16)
    text_joined = "\n".join(lines)

    # اعمل 45 فريم مع تأثير زووم خفيف
    for i in range(45):
        # خلفية بتتغير
        r = 20 + i
        img = Image.new('RGB', (W, H), (r, 10, 60))
        draw = ImageDraw.Draw(img)

        # مستطيل
        draw.rectangle([(30, 350-i), (1050, 1550+i)], fill=(0,0,0), outline=(150,100,255), width=6)

        # النص في النص
        y = 600
        for line in lines:
            bbox = draw.textbbox((0,0), line, font=font_big)
            w = bbox[2]-bbox[0]
            x = (W - w)//2
            draw.text((x, y), line, fill=(255,255,255), font=font_big)
            y += 130

        frames.append(img)

    imageio.mimsave(out, frames, fps=15, macro_block_size=1)
    return out

@bot.message_handler(commands=['start','help'])
def start(m):
    bot.reply_to(m, "🎬 V5 VIDEO! \nاكتب:\n/make نص الفيديو\nورح ابعتلك MP4 حقيقي!")

@bot.message_handler(commands=['make'])
def make(m):
    txt = m.text.replace("/make","").strip()
    if not txt: txt = "الإنسان لقى زر الإطفاء السري!"
    bot.send_message(m.chat.id, f"⏳ بصنع فيديو MP4: {txt}")
    path = make_video_from_text(txt)
    with open(path, 'rb') as v:
        bot.send_video(m.chat.id, v, caption=f"🎥 جاهز! {txt}\n#humanvsai #fyp")

app = Flask(__name__)
@app.route('/')
def home(): return "V5 Video Bot Live"
def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT",10000)))
threading.Thread(target=run_web, daemon=True).start()
bot.infinity_polling()
