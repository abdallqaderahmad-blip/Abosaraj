import os, json, logging, requests
from flask import Flask, request
from fal_client import subscribe
from groq import Groq

TOKEN = os.getenv('TELEGRAM_TOKEN')
GROQ_KEY = os.getenv('GROQ_API_KEY')
FAL_KEY = os.getenv('FAL_KEY')
if FAL_KEY:
    os.environ['FAL_KEY'] = FAL_KEY

groq_client = Groq(api_key=GROQ_KEY)
flask_app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def tg_send(cid, txt):
    u = 'https://api.telegram.org/bot' + TOKEN + '/sendMessage'
    requests.post(u, json={'chat_id': cid, 'text': txt[:4000]}, timeout=20)

def tg_photo(cid, url, cap=''):
    u = 'https://api.telegram.org/bot' + TOKEN + '/sendPhoto'
    requests.post(u, json={'chat_id': cid, 'photo': url, 'caption': cap}, timeout=50)

def get_scenes(story):
    p = 'Return ONLY JSON array of 8 short english cartoon prompts. Story: ' + story
    c = groq_client.chat.completions.create(
        model='openai/gpt-oss-20b',
        messages=[{'role': 'user', 'content': p}],
        max_tokens=2000
    )
    raw = c.choices[0].message.content.strip()
    a = raw.find('[')
    b = raw.rfind(']') + 1
    data = json.loads(raw[a:b])
    out = []
    for x in data:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict) and 'prompt' in x:
            out.append(x['prompt'])
    return out[:8]

def gen_image(pr):
    # حاول fal اول
    try:
        if FAL_KEY:
            r = subscribe('fal-ai/flux/dev', arguments={'prompt': pr,
