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

# ===== موقع الويب =====
flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>أبو سراج - محول القصص لصور</title>
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@700&display=swap" rel="stylesheet">
    <style>
    body{font-family:'Tajawal',sans-serif; background:#0f0f0f; color:white; text-align:center; margin:0}
    .hero{padding:80px 20px; background:linear-gradient(135deg,#667eea,#764ba2)}
    .hero h1{font-size:50px; margin:0}
    .btn{display:inline-block; margin-top:25px; background:#fff; color:#764ba2; padding:16px 40px; border-radius:30px; text-decoration:none; font-weight:bold; font-size:20px}
    .features{display:flex; justify-content:center; gap:25px; padding:50px 20px; flex-wrap:wrap}
    .card{background:#1a1a1a; padding:30px; border-radius:15px; width:260px; border:1px solid #333}
    .status{padding:20px; opacity
