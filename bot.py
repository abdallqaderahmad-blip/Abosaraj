import os,json,requests
from flask import Flask,request
from fal_client import subscribe
from groq import Groq

TOKEN=os.getenv('TELEGRAM_TOKEN')
GROQ_KEY=os.getenv('GROQ_API_KEY')
FAL_KEY=os.getenv('FAL_KEY')
if FAL_KEY:
 os.environ['FAL_KEY']=FAL_KEY

groq_client=Groq(api_key=GROQ_KEY)
app=Flask(__name__)

def tg_send(cid,txt):
 u='https://api.telegram.org/bot'+TOKEN+'/sendMessage'
 d=dict()
 d['chat_id']=cid
 d['text']=txt
 requests.post(u,json=d,timeout=20)

def tg_photo(cid,url):
 u='https://api.telegram.org/bot'+TOKEN+'/sendPhoto'
 d=dict()
 d['chat_id']=cid
 d['photo']=url
 requests.post(u,json=d,timeout=50)

def get_scenes(story):
 pr='Return JSON array 8 prompts. Story:'+story
 c=groq_client.chat.completions.create(
  model='openai/gpt-oss-20b',
  messages=[dict(role='user',content=pr)],
  max_tokens=2000
 )
 raw=c.choices[0].message.content.strip()
 a=raw.find('[')
 b=raw.rfind(']')+1
 data=json.loads(raw[a:b])
 out=[]
 for x in data:
  if isinstance(x,str):
   out.append(x)
 return out[:8]

def gen_image(pr):
 try:
  args=dict()
  args['prompt']=pr
  args['image_size']='landscape_16_9'
  r=subscribe('fal-ai/flux/dev',arguments=args)
  return r['images'][0]['url']
 except:
  safe=requests.utils.quote(pr)
  base='https://image.pollinations.ai/prompt/'
  tail='?w=1024&h=576&model=flux&nologo=true'
  return base+safe+tail

@app.route('/')
def home():
 return 'Live'

@app.route('/webhook',methods=['POST'])
def webhook():
 data=request.get_json(force=True)
 msg=data.get('message')
 if not msg:
  return 'ok',200
 cid=msg['chat']['id']
 txt=msg.get('text','')
 if txt.startswith('/start'):
  tg_send(cid,'Ready')
  return 'ok',200
 if len(txt)<15:
  tg_send(cid,'Short')
  return 'ok',200
 tg_send(cid,'Analyzing')
 scenes=get_scenes(txt)
 tg_send(cid,'Drawing')
 for p in scenes:
  url=gen_image(p)
  tg_photo(cid,url)
 tg_send(cid,'Done')
 return 'ok',200

if __name__=='__main__':
 wh='https://abosaraj.onrender.com/webhook'
 api='https://api.telegram.org/bot'+TOKEN+'/setWebhook?url='+wh
 try:
  requests.get(api,timeout=10)
 except:
  pass
 port=int(os.environ.get('PORT',10000))
 app.run(host='0.0.0.0',port=port)
