def build(chat_id, lines):
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("part_*.mp4")+["FINAL.mp4","list.txt"]:
            try: os.remove(f)
            except: pass

        tg_send(chat_id,f"⏳ ببلش {len(lines)} مشاهد...")
        part_files=[]
        for i,line in enumerate(lines[:3]):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; part=f"part_{i}.mp4"
            tg_send(chat_id,f"🎬 مشهد {i+1}: {line[:30]}...")
            
            try:
                if PIXABAY_KEY:
                    url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={get_keyword(line)}&per_page=10"
                    data=requests.get(url,timeout=10).json()
                    if data.get("hits"):
                        vurl=random.choice(data["hits"])["videos"]["small"]["url"]
                        open(v,"wb").write(requests.get(vurl,timeout=30).content)
                    else: raise Exception()
                else: raise Exception()
            except:
                open(v,"wb").write(requests.get("https://cdn.pixabay.com/video/2020/07/30/45549-442790323_small.mp4",timeout=30).content)

            gTTS(text=line, lang='ar', slow=False).save(a)
            
            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            dur=ac.duration+0.4
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)
            
            vc=vc.fx(vfx.colorx, 0.88) # ألوان غامقة - مصحح
            
            vc.set_audio(ac).write_videofile(part, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", threads=1, logger=None)
            vc.close(); ac.close()
            part_files.append(part)
            tg_send(chat_id,f"✅ مشهد {i+1} جاهز")

        tg_send(chat_id,"✂️ بجمع بطريقة خفيفة...")
        with open("list.txt","w") as f:
            for p in part_files: f.write(f"file '{p}'\n")
        subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True)

        tg_send(chat_id,"📤 برفع الفيديو النهائي...")
        tg_send_video(chat_id,"FINAL.mp4")
    except Exception as e:
        tg_send(chat_id,f"❌ {e}\n{traceback.format_exc()[:1200]}")
