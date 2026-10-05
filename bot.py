import os, json, threading, requests, time
from flask import Flask
from google import genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI = os.getenv("GEMINI_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL
client = genai.Client(api_key=GEMINI)

flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    html = '''
    <!DOCTYPE html><html lang="ar" dir="rtl"><head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ابو سراج</title>
    <style>
    body{font-family:sans-serif;background:#0f0f0f;color:white;text-align:center;margin:0}
    .hero{padding:70px 20px;background:linear-gradient(135deg,#667eea,#764ba2)}
    .btn{display:inline-block;margin-top:20px;background:#fff;color:#764ba2;padding:14px 32px;border-radius:30px;text-decoration:none;font-weight:bold}
    .features{display:flex;justify-content:center;gap:20px;padding:40px 20px;flex-wrap:wrap}
    .card{background:#1a1a1a;padding:25px;border-radius:12px;width:240px}
    </style></head><body>
    <div class="hero"><h1>🎨 أبو سراج</h1><p>حول أي قصة إلى 8 صور كرتونية بثواني</p>
    <a class="btn" href="https://t.me/YOUR_BOT_HERE" target="_blank">🚀 افتح البوت في تلغرام</a></div>
    <div class="features">
    <div class="card"><h3>✍️ اكتب قصة</h3><p>أرسل فكرتك</p></div>
    <div class="card"><h3>🤖 ذكاء اصطناعي</h3><p>Gemini + Flux</p></div>
    <div class="card"><h3>🖼️ 8 مشاهد</h3><p>صور جاهزة</p></div>
    </div><p style="opacity:.5;padding:20px">Bot Live ✅</p></body></html>
    '''
    return html

def run_flask():
   
