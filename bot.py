import os
import json
import threading
from flask import Flask
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from fal_client import subscribe

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI = os.getenv("GEMINI_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL

genai.configure(api_key=GEMINI)
model = genai.GenerativeModel("gemini-1.5-flash")

flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return "Bot Live", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 أهلاً! ابعت القصة")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = update.message.text
    await update.message.reply_text("⏳ جاري تحليل قصتك وتقسيمها لـ 8 مشاهد...")
    try:
        prompt = f'''حلل القصة ل 8 مشاهد، رجع JSON فقط:
        {{"title":"x","scenes":[{{"prompt":"English cinematic video prompt"}}]}}
        القصة: {story}'''
        resp = model.generate_content(prompt)
        txt = resp.text.replace("```json","").replace("```","").strip()
        data = json.loads(txt)
        for i, sc in enumerate(data.get("scenes",[])[:8], 1):
            await update.message.reply_text(f"🎥 بعمل مشهد {i}/8")
            r = subscribe("fal-ai/kling-video/v2.1/master/text-to-video", arguments={"prompt": sc["prompt"]})
            url = r.get("video",{}).get("url") if isinstance(r.get("video"), dict) else r.get("url")
            if url:
                await update.message.reply_video(url, caption=f"مشهد {i}")
        await update.message.reply_text("✅ خلصت!")
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot Started...")
    app.run_polling()
