# -*- coding: utf-8 -*-
import os
import json
import logging
import requests
from flask import Flask, request
import telegram
from fal_client import subscribe
from groq import Groq

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")
if FAL_KEY:
    os.environ["FAL_KEY"] = FAL_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_KEY)
bot = telegram.Bot(token=TOKEN)
flask_app = Flask(__name__)

def get_scenes(story):
    prompt = "Return ONLY JSON array 8 scenes: [{\"scene\":1,\"prompt\":\"cartoon prompt\"}] Story: " + story
    comp = groq_client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[{"role": "user", "content":
