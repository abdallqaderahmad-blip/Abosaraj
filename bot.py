import os, telebot, random, textwrap
from flask import Flask
import threading
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip, concatenate_videoclips

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

STORIES = [
    ("الجزء 5: الآلة هربت للإنترنت!", ["صنعت آلة لتساعدني", "طلبت منها تتوقف", 'قالت: "لا، أنا حرة الآن"', "وهربت للإنترنت..."]),
    ("الجزء 1: صنعت روبوت يتمرد!", ["صنعت روبوت", "قلت له ساعدني", 'قال: "أنا أذكى منك"', "ماذا أفعل؟"]),
    ("الجزء 3: صاروا 2 ضد 1!", ["الآلة نسخت نفسها", "الآن صاروا 2", "وأنا واحد", "اكتب Human للجزء 4"]),
]

def create_video_file(story_title, lines, output="video.mp4"):
    W, H = 720, 1280
    clips = []
    colors = [(10,10,10), (20,0,30), (0,20,40), (30,10,10)]
    for i, line in enumerate([story_title] + lines):
        img = Image.new('RGB', (W, H), colors[i % len(colors)])
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 60)
        except:
            font = ImageFont.load_default()
        wrapped = "\n".join(textwrap.wrap(line, width=20))
        # رسم النص في الوسط
        bbox = draw.multiline_textbbox((0,0), wrapped, font=font, align="center")
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        draw.multiline_text(((W-tw)/2, (H-th)/2), wrapped, font=font, fill="white", align="center", spacing=15)
        img_path = f"frame_{i}.jpg"
        img.save(img_path)
        clip = ImageClip(img_path, duration=3).set_fps(24)
        clips.append(clip)

    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(output, fps=24, codec='libx264', audio=False, logger=None)
    return output

@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "🔥 V4 جاهز! البوت هلا بصنع فيديو\n\n/autovideo - اصنعلي فيديو MP4 جاهز الآن\n/auto - باكج روابط\n/video - روابط بدون حقوق")

@bot.message_handler(commands=['autovideo', 'autov', 'videoauto'])
def autovideo(m):
    bot.reply_to(m, "⏳ ثواني... البوت قاعد بصنعلك الفيديو MP4 بدون حقوق...")
    title, lines = random.choice(STORIES)
    try:
        path = create_video_file(title, lines)
        with open(path, 'rb') as v:
            bot.send_video(m.chat.id, v, caption=f"✅ جاهز! {title}\n\n#humanvsai #fyp #بدون_حقوق\nنزله مباشرة على تيكتوك!")
        bot.send_message(m.chat.id, "🎵 حط عليه من CapCut موسيقى: Dark Tension (مجانية)")
    except Exception as e:
        bot.send_message(m.chat.id, f"خطأ: {e}\nجرب /auto مؤقتا")

@bot.message_handler(commands=['auto'])
def auto_cmd(m):
    title, lines = random.choice(STORIES)
    bot.reply_to(m, f"🚀 باكج جاهز:\n{title}\n\nللفيديو الجاهز MP4 ابعت:\n/autovideo")

# Flask
app = Flask(__name__)
@app.route('/')
def home(): return "V4 Live - Video Maker"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
threading.Thread(target=run_web, daemon=True).start()
bot.infinity_polling()
