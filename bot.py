from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import time
import requests

BOT_TOKEN = "8771400723:AAHqHY6bxL-h76TdvNI8GK772ZyqhYNtifs"
# موديل توليد فيديو بدون علامة مائية (مجاني)
VIDEO_API = "https://api.example.com/video" # رح نستبدله بـ API حقيقي

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("اكتب هيك: /video طيارة فوق جبال ضبابية")
        return

    await update.message.reply_text(f"🎬 عم ولد فيديو 30 ثانية لـ: {prompt}\nبدون علامة مائية... انتظر شوي ⏳")

    # هنا المنطق يلي عملناه فوق:
    # 1. توليد 5 ثواني اولية
    # 2. تمديد 5 مرات ليصير 30 ثانية
    # (الكود الحقيقي بيستخدم Replicate / Runway API)

    # مثال تجريبي - بيرجع فيديو وهمي
    # انت بتبدلو برابط توليد حقيقي
    video_url = f"https://...generate?prompt={prompt}&duration=30&watermark=false"
    
    await update.message.reply_video(
        video=video_url,
        caption=f"✅ فيديو 30 ثانية جاهز\n📝 {prompt}\nبدون علامة مائية"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلا ببوت Human vs Machine 🤖\n\n"
        "الأوامر:\n"
        "/video [وصف] - فيديو 30 ثانية بدون لوجو\n"
        "/img [وصف] - صورة 4K بدون لوجو\n"
        "/help - المساعدة"
    )

app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("video", video_command))

print("البوت شغال...")
app.run_polling()
