import os
import json
import threading
from flask import Flask
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI = os.getenv("GEMINI_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL

genai.configure(api_key=GEMINI)
model = genai.GenerativeModel("gemini-2.0-flash")

flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return "Bot Live", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا بك في بوت أبو سراج لقصص الأطفال! 🎉\n\nأرسل قصة وسأحولها لـ 8 مشاهد مصورة.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = update.message.text
    await update.message.reply_text(f"⏳ جاري تحليل قصتك وتقسيمها لـ 8 مشاهد...\n\nالقصة: {story[:100]}...")

    prompt = f"""
    حلل هذه القصة وقسمها لـ 8 مشاهد. كل مشهد برومبت انجليزي لتوليد صورة كرتونية للأطفال بنفس الشخصية.
    أرجع JSON فقط بهذا الشكل: [{{"scene":1,"prompt":"..."}},...]
    القصة: {story}
    """
    try:
        response = model.generate_content(prompt)
        text = response.text.replace("```json","").replace("```","").strip()
        scenes = json.loads(text)

        for s in scenes[:8]:
            await update.message.reply_text(f"🎨 جاري رسم المشهد {s['scene']}/8...")
            try:
                result = subscribe(
                    "fal-ai/flux/dev",
                    arguments={"prompt": s['prompt'] + ", children cartoon style, cute, vibrant colors, consistent character"}
                )
                img_url = result['images'][0]['url']
                await update.message.reply_photo(photo=img_url, caption=f"المشهد {s['scene']}: {s['prompt'][:100]}")
            except Exception as e:
                await update.message.reply_text(f"خطأ في المشهد {s['scene']}: {e}")

        await update.message.reply_text("✅ خلصت القصة! أرسل قصة جديدة.")
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print("Bot Started...")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)
