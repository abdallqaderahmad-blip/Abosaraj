import os
import json
import logging
import requests
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe
from groq import Groq

# ================= CONFIG =================
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")

if FAL_KEY:
    os.environ["FAL_KEY"] = FAL_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_KEY)

flask_app = Flask(__name__)
telegram_app = ApplicationBuilder().token(TOKEN).build()

# ================= PROMPTS =================
SYSTEM_PROMPT = """
You are a professional children's story scene extractor.
Your job: Convert a story into exactly 8 vivid cartoon scenes.
Return ONLY valid JSON array.
Format: [{"scene": 1, "prompt": "detailed english cartoon prompt"},...]
Rules:
- Prompts MUST be in English
- Style: cute cartoon storybook, vibrant colors, soft lighting, Pixar style
- Keep same main character across all scenes
- Describe background and action clearly
- Exactly 8 scenes, no more no less
- No extra text outside JSON
"""

def get_scenes_from_groq(story_text):
    user_prompt = SYSTEM_PROMPT + "\n\nStory to convert:\n" + story_text + "\n\nReturn JSON only, 8 scenes."
    completion = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a JSON generator. Return only valid JSON array."},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.7,
        max_tokens=2500
    )
    raw_text = completion.choices[0].message.content.strip()
    logger.info("Groq raw: %s", raw_text[:300])
    start_idx = raw_text.find("[")
    end_idx = raw_text.rfind("]") + 1
    if start_idx == -1 or end_idx == 0:
        raise ValueError("Groq did not return JSON. Output: " + raw_text[:500])
    json_str = raw_text[start_idx:end_idx]
    scenes = json.loads(json_str)
    if len(scenes) < 4:
        raise ValueError("Too few scenes returned")
    return scenes[:8]

def generate_image_fal(prompt_en):
    full_prompt = prompt_en + ", cute cartoon storybook illustration, Pixar style, vibrant colors, soft lighting, highly detailed, 4k, cheerful"
    result = subscribe(
        "fal-ai/flux/dev",
        arguments={
            "prompt": full_prompt,
            "image_size": "landscape_16_9",
            "num_images": 1,
            "num_inference_steps": 28
        }
    )
    image_url = result["images"][0]["url"]
    logger.info("Image generated: %s", image_url[:100])
    return image_url

# ================= TELEGRAM =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "مرحبا يا أبو سراج! 🥕✨\n\n"
        "أنا بوت تحويل القصص لصور كرتونية دسمة\n"
        "أرسل لي أي قصة قصيرة\n"
        "مثال: كان هناك أرنب صغير يبحث عن جزرة ذهبية في غابة سحرية\n\n"
        "ورح أحولها لـ 8 صور كرتونية متتابعة كأنها فيلم 🎬🎨\n\n"
        "الأوامر:\n"
        "/start - رسالة الترحيب\n"
        "/help - كيف أستخدم البوت\n"
        "/story - مثال لقصة"
    )
    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "كيف تستخدم البوت:\n"
        "1. اكتب قصة قصيرة (3-5 أسطر)\n"
        "2. أرسلها هنا\n"
        "3. انتظر، رح أحللها بـ Groq وأرسمها بـ Flux\n"
        "4. رح توصلك 8 صور واحدة ورا الثانية\n\n"
        "نصيحة: اذكر الشخصيات والمكان بوضوح"
    )
    await update.message.reply_text(help_text)

async def story_example(update: Update, context: ContextTypes.DEFAULT_TYPE):
    example = "كان هناك أرنب صغير اسمه بوبو يبحث عن جزرة ذهبية في غابة سحرية مليئة بالأشجار المضيئة، واجه ثعلب ماكر، عبر نهر لامع، وتسلق جبل عالي حتى وجد الجز
