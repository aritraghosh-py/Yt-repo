import json
import edge_tts
import asyncio
import os
from gtts import gTTS

def clean_text_for_audio(text):
    return text.replace('*', '').replace('#', '').replace('"', '')

async def generate_voiceover(json_filename):
    with open(json_filename, 'r') as f:
        data = json.load(f)
    
    full_script = " ".join([seg['text'] for seg in data['segments']])
    full_script = clean_text_for_audio(full_script)
    
    folder_name = json_filename.replace(".json", "")
    audio_path = f"assets/{folder_name}/voiceover.mp3"
    subtitle_path = f"assets/{folder_name}/subtitles.vtt"
    os.makedirs(os.path.dirname(audio_path), exist_ok=True)
    
    print(f"🗣️ Generating Audio (1.2x Speed)...")

    # PLAN A: Edge-TTS
    try:
        # rate="+20%" is roughly 1.2x speed
        communicate = edge_tts.Communicate(full_script, "en-US-ChristopherNeural", rate="+20%")
        submaker = edge_tts.SubMaker()
        
        with open(audio_path, "wb") as file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    submaker.feed(chunk)
                    
        with open(subtitle_path, "w", encoding="utf-8") as file:
            file.write(submaker.generate_subs())
            
    except Exception as e:
        # PLAN B: Google TTS (No speed control in standard gTTS, but reliable)
        print(f"   ⚠️ Edge Failed. Switching to Google Backup...")
        tts = gTTS(text=full_script, lang='en')
        tts.save(audio_path)

    # Verify
    if os.path.exists(audio_path):
        data['audio_path'] = audio_path
        if os.path.exists(subtitle_path):
            data['subtitle_path'] = subtitle_path
        with open(json_filename, 'w') as f:
            json.dump(data, f, indent=4)
    else:
        raise Exception("Audio failed.")

if __name__ == "__main__":
    pass
