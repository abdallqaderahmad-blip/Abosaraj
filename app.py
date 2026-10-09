import os
import asyncio
import requests
import random
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from moviepy.editor import *
import edge_tts

BOT_TOKEN = os.getenv("BOT_TOKEN")
PEXELS_KEY = os.getenv("PEXELS_KEY")

SCENES = [
    {"keyword": "lonely girl dark room portrait", "text": "جلست ظل وحيدة... في عتمة لا يراها أحد"},
    {"keyword": "girl window rain sad portrait", "text": "كانت تبحث عن ظل... يحميها من برد الوحدة"},
    {"keyword": "crowd street alone girl portrait", "text": "في الشارع... الجميع يمضي ولا أحد يلتفت"},
    {"keyword": "mother praying light portrait", "text": "ثم جاء صوت أمها... بدعاء يشبه النور"}
]

MUSIC_URL = "https://cdn.pixabay.com/download/audio/2022/05/27/audio_1808fbf07a.mp3"

async def make_voice(text, output="voice.mp3"):
    voice = "ar-SA-ZaydNeural"
    communicate = edge_tts.Communicate(text, voice, rate="-15%", pitch="-8Hz", volume="+10%")
    await communicate.save(output)
    return output

def download_pexels(keyword, filename):
    headers = {"Authorization": PEXELS_KEY}
    params = {"query": keyword, "per_page": 10, "orientation": "portrait", "size": "medium"}
    r = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=20)
    data = r.json()
    if not data.get("videos"):
        raise Exception(f"No video for {keyword}")
    video = random.choice(data["videos"])
    # دور على فيديو عمودي
    portrait_files = [f for f in video["video_files"] if f["width"] < f["height"]]
    file_link = portrait_files[0]["link"] if portrait_files else video["video_files"][0]["link"]
    with requests.get(file_link, stream=True, timeout=30) as resp:
        with open(filename, "wb") as f:
            for chunk in resp.iter_content(1024*1024):
                f.write(chunk)
    return filename

def build_final():
    clips = []
    for i, sc in enumerate(SCENES):
        v_file = f"scene_{i}.mp4"
        a_file = f"voice_{i}.mp3"
        print(f"Downloading {sc['keyword']}...")
        download_pexels(sc["keyword"], v_file)
        print(f"Making voice {i}...")
        asyncio.run(make_voice(sc["text"], a_file))

        vc = VideoFileClip(v_file).subclip(0,5).resize((1080,1920)).set_duration(5)
        vc = vc.fx(vfx.speedx, 0.85).resize(lambda t: 1 + 0.08*t).fx(vfx.colorx, 0.85)
        ac = AudioFileClip(a_file)
        txt = TextClip(sc["text"], fontsize=55, color='white', font='DejaVu-Sans-Bold', method='caption', size=(900, None), stroke_color='black', stroke_width=2)
        txt = txt.set_duration(5).set_position(('center', 1400))
        vc = CompositeVideoClip([vc, txt]).set_audio(ac)
        clips.append(vc)

    final_video = concatenate_videoclips(clips, method="compose")
    try:
        r = requests.get(MUSIC_URL, timeout=20)
        open("bg.mp3","wb").write(r.content)
        bg = AudioFileClip("bg.mp3").subclip(0, final_video.duration).volumex(0.10).audio_fadein(1).audio_fadeout(2)
        final_audio = CompositeAudioClip([bg, final_video.audio])
        final_video = final_video.set_audio(final_audio)
    except Exception as e:
        print(f"Music error {e}")

    final_video.write_videofile("V10_ZEL_FINAL.mp4", fps=24, codec="libx264", audio_codec="aac", preset="ultrafast")
    return "V10_ZEL_FINAL.mp4"

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 V10 مصنع ظل - الراوي الغامض\n📦 4 مشاهد عمودية 9:16 + صوت رجل + موسيقى\n⏳ ببني الفيديو 20ث... استنى 3-4 دقايق")
    try:
        path = build_final()
        await update.message.reply_video(video=open(path,"rb"), caption="🎥 V10 ظل - جاهز للريلز\n🎙️ راوي غامض + كتابة متناسقة")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

async def zel_arabic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await zel(update, context)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("zel", zel))
    app.add_handler(MessageHandler(filters.Regex(r'^(ظل|/ظل)$'), zel_arabic))
    print("V10 Factory Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
