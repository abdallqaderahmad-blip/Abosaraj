# -*- coding: utf-8 -*-
import os, json, logging, requests, traceback
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
    requests.post(url, json={'chat_id': chat_id, 'text': text[:4000]}, timeout=20)

def tg_photo(chat_id, photo_url, caption):
    url = 'https://api.telegram.org/bot' + TOKEN + '/sendPhoto'
    r = requests.post(url, json={'chat_id': chat_id, 'photo': photo_url, 'caption': caption}, timeout=40)
    logger.info('photo resp: ' + r.text[:500])
    return r

def get_scenes(story):
    prompt = 'Return ONLY a JSON array of 8 strings, each is an english cartoon image prompt for this story. No explanation. Story: ' + story
    comp = groq_client.chat.completions.create(
        model='openai/gpt-oss-20b',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=2500
    )
    raw = comp.choices[0].message.content.strip()
    logger.info('Groq raw: ' + raw[:1000])
    a = raw.find('[')
    b = raw.rfind(']') + 1
    if a == -1:
        raise ValueError('No JSON from Groq: ' + raw[:300])
    data = json.loads(raw[a:b])
    # normalize to list of dicts
    scenes = []
    for i, item in enumerate(data, 1):
        if isinstance(item, str):
            scenes.append({'prompt': item})
        elif isinstance(item, dict):
            scenes.append(item)
    return scenes[:8]

def gen_image(p):
    if not FAL_KEY:
        raise ValueError('FAL_KEY missing in Render env vars')
    r = subscribe('fal-ai/flux/dev', arguments={'prompt': p + ', cute cartoon storybook style, vibrant', 'image_size': 'landscape_16_9'})
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
            return 'ok', 200
        if len(text) < 15:
            tg_send(chat_id, 'Story too short')
            return 'ok', 200
        tg_send(chat_id, 'Analyzing with Groq...')
        try:
            scenes = get_scenes(text)
        except Exception as e:
            tg_send(chat_id, 'Groq error: ' + str(e))
            return 'ok', 200
        tg_send(chat_id, f'Found {len(scenes)} scenes, drawing...')
        for idx, s in enumerate(scenes, 1):
            pr = s.get('prompt', '') if isinstance
