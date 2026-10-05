from flask import Flask
import threading
app = Flask('')
@app.route('/')
def home(): return "Bot is Alive!"
def run(): app.run(host='0.0.0.0', port=10000)
threading.Thread(target=run).start()
