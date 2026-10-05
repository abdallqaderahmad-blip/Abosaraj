# -*- coding: utf-8 -*-
import os
import json
import logging
import requests
from flask import Flask, request
from fal_client import subscribe
from groq import Groq

TOKEN = os.getenv('TELEGRAM_TOKEN')
GROQ_KEY = os.getenv('GROQ_API_KEY')
FAL_KEY = os.getenv('FAL_KEY')
if FAL_KEY:
    os.environ['FAL_KEY'] = FAL_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
groq_client = Groq(api_key=GROQ_KEY)
flask_app = Flask(__name__)

def tg_send(chat_id, text):
    url = 'https://api.telegram.org/bot' + TOKEN + '/sendMessage'
    requests.post(url, json={'chat_id': chat_id, 'text': text}, timeout=20)

def tg_photo(chat_id, photo_url, caption):
    url = 'https://api.telegram.org/bot' + TOKEN + '/sendPhoto'
    requests.post(url, json={'chat_id': chat_id, 'photo': photo_url, 'caption': caption}, timeout=40)

def get_scenes(story):
    prompt = 'Return ONLY JSON array of 8 english prompts. Story: ' + story
    comp = groq_client.chat.completions.create(
        model='openai/gpt-oss-20b',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=2000
    )
    raw = comp.choices[0].message.content.strip()
    a = raw.find('[')
    b = raw.rfind(']') + 1
    data = json.loads(raw[a:b])
    out = []
    for item in data:
        if isinstance(item, str):
            out.append(item)
        else:
            if 'prompt' in item:
                out.append(item['prompt'])
    return out

def gen_image(p):
    r = subscribe('fal-ai/flux/dev', arguments={'prompt': p, 'image_size': 'landscape_16_9'})
    return r['images'][0]['url']

@flask_app.route('/')
def home():
    return 'Bot Live'

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        if 'message' not in data:
            return 'ok', 200
        chat_id = data['message']['chat']['id']
        text = data['message'].get('text', '')
        if text.startswith('/start'):
            tg_send(chat_id, 'Ready! Send story')
            return 'ok',
