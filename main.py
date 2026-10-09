# مصنع مونتاج ظل - Thill Montage Factory
# جوال جديد + جيميل واحد = thill.legacy.work@gmail.com

import asyncio
import os
from moviepy.editor import VideoFileClip, concatenate_videoclips, CompositeVideoClip, CompositeAudioClip, TextClip, vfx
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import edge_tts

BOT_TOKEN = "حط_توكنك_من_BotFather_هنا"
VIDEOS = []

# البيانات المجمعة - تقسيم 20 ثانية
DATA = [
    {"file": "scene_1.mp4", "narration": "طردوه صغيراً... قالوا بلا قوة", "slow_at": 4.0, "slow_speed": 0.6},
    {"file": "scene_2.mp4", "narration": "لكن الحارس الأبيض... كان ينتظره ثمانية عشر عاماً", "slow_at": 4.0, "slow_speed": 0.6},
    {"file": "scene_3.mp4", "narration": "في القصر... شعرت بختمها يحترق", "slow_at": 4.0, "slow_speed": 0.6},
    {"file": "scene_4.mp4", "narration": "وعاد الوريث... ليسترد عرشه", "slow_at": 3.5, "slow_speed": 0.4},
]

async def make_voice(text, filename):
    # صوت غامض عميق - Hamed
    communicate = edge_tts.Communicate(text, "ar-SA-HamedNeural", rate="-15%", pitch="-8Hz")
    await communicate.save(filename)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    VIDEOS.clear()
    await update.message.reply_text(
        "🔥 مصنع مونتاج ظل جاهز!\n\n"
        "📦 البيانات المطلوبة:\n"
        "ارفع 4 فيديوهات بالترتيب:\n"
        "1️⃣ ظل مطرود - 5ث\n"
        "2️⃣ لقاء الذئب سيف - 5ث\n"
        "3️⃣ ليان والختم - 5ث\n"
        "4️⃣ انفجار العيون الزرقاء - 5ث\n\n"
        "⚙️ أنا حعمل:\n"
        "✓ تمديد حركة آخر ثانية (شد انتباه)\n"
        "✓ راوي غامض لكل مشهد\n"
        "✓ نص على الشاشة متزامن\n"
        "✓ نهاية: الجزء 2 غداً 8 مساءً 👑"
    )

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await context.bot.get_file(update.message.document or update.message.video or update.message.animation)
    path = f"scene_{len(VIDEOS)+1}.mp4"
    await file.download_to_drive(path)
    VIDEOS.append(path)

    idx = len(VIDEOS)-1
    await update.message.reply_text(f"✅ مشهد {len(VIDEOS)} وصل:\n🎙️ \"{DATA[idx]['narration']}\" - تمديد {DATA[idx]['slow_speed']}x")

    if len(VIDEOS) == 4:
        await update.message.reply_text("⏳ بجمع البيانات وبعمل المونتاج... 40 ثانية")
        await create_final(update)

async def create_final(update: Update):
    clips = []
    for i, vid_path in enumerate(VIDEOS):
        info = DATA[i]
        clip = VideoFileClip(vid_path).subclip(0, 5)

        # تمديد حركة - شد انتباه
        main = clip.subclip(0, info["slow_at"])
        slow = clip.subclip(info["slow_at"], 5).fx(vfx.speedx, info["slow_speed"])
        clip = concatenate_videoclips([main, slow])

        # صوت راوي
        voice_file = f"voice_{i}.mp3"
        await make_voice(info["narration"], voice_file)
        voice = AudioFileClip(voice_file).set_start(0.4)

        # نص غامض
        txt = TextClip(info["narration"], fontsize=55, color='white', font='Arial-Bold', stroke_color='black', stroke_width=3, method='caption', size=(clip.w*0.9, None))
        txt = txt.set_duration(clip.duration).set_position(('center', 0.75), relative=True)

        # دمج
        comp = CompositeVideoClip([clip, txt])
        # خفض صوت الفيديو الأصلي ورفع الراوي
        if comp.audio:
            comp_audio = comp.audio.volumex(0.2)
            comp = comp.set_audio(CompositeAudioClip([comp_audio, voice]))
        else:
            comp = comp.set_audio(voice)

        clips.append(comp)

    final = concatenate_videoclips(clips, method="compose")

    # نهاية - الجزء التاني
    end = TextClip("عاد الوريث...\n\nالجزء 2 - غداً 8 مساءً 👑", fontsize=70, color='#00D4FF', font='Arial-Bold', stroke_color='black', stroke_width=4, method='caption', size=(final.w*0.9, None))
    end = end.set_duration(2.5).set_position('center').set_audio(final.audio)

    final_video = concatenate_videoclips([final, end])

    final_video.write_videofile("thill_ep1_final_20s.mp4", fps=24, codec='libx264', audio_codec='aac')

    await update.message.reply_document(
        document=open("thill_ep1_final_20s.mp4", 'rb'),
        caption="🔥 جاهز للنشر - 20 ثانية\n\nTikTok / Reels / Shorts\n#ظل #ThillLegacy\n\nالجزء 2 بكرا 8 مساءً"
    )

    # تنظيف
    for f in VIDEOS + [f"voice_{i}.mp3" for i in range(4)] + ["thill_ep1_final_20s.mp4"]:
        if os.path.exists(f): os.remove(f)
    VIDEOS.clear()

if __name__ == "__main__":
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("مونتاج", start))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO | filters.Document.ALL, handle_video))
    print("🚀 مصنع مونتاج ظل شغال...")
    app.run_polling()
