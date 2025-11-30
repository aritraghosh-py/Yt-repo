import google.generativeai as genai
import json
import os
import re
import time

# Setup API Key
api_key = os.environ.get("GOOGLE_API_KEY")
genai.configure(api_key=api_key)

def sanitize_filename(name):
    clean_name = re.sub(r'[^\w\s-]', '', name)
    return clean_name.strip().replace(' ', '_')

def generate_with_fallback(prompt):
    """
    Tries models in order of quality: 2.5 -> 1.5 -> Pro
    """
    models_to_try = [
        'gemini-2.5-flash',      # <--- RESTORED: The Best Model
        'gemini-1.5-flash',      # Backup 1
        'gemini-1.5-pro-latest', # Backup 2
        'gemini-pro'             # Last Resort
    ]
    
    for model_name in models_to_try:
        try:
            print(f"   🤖 Trying model: {model_name}...")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"   ⚠️ {model_name} failed. Trying next...")
            time.sleep(1)
            
    raise Exception("❌ ALL models failed. Check your API Key permissions.")

def get_viral_topic(history_file="history.txt"):
    if os.path.exists(history_file):
        with open(history_file, "r") as f:
            existing_topics = f.read()
    else:
        existing_topics = ""

    prompt = f"""
    You are a YouTube Strategist for 'Echoes of Reality'.
    Generate ONE viral, dark mystery, paradox, or cosmic horror topic.
    Rules:
    1. It must be scary, mysterious, or mind-blowing.
    2. It must NOT be in this list: {existing_topics}
    3. Return ONLY the topic name (no quotes).
    """
    
    try:
        topic_text = generate_with_fallback(prompt)
        topic = topic_text.strip()
        
        # Save to history
        with open(history_file, "a") as f:
            f.write(topic + "\n")
            
        print(f"🤖 Topic Selected: {topic}")
        return topic
        
    except Exception as e:
        print(f"❌ Brain Error: {e}")
        return "The Dark Forest Theory" 

def generate_free_script(topic):
    # --- THE RETENTION UPGRADE ---
    prompt = f"""
    You are an elite viral screenwriter. Write a STRICTLY 40-SECOND YouTube Shorts script about: {topic}.
    
    CRITICAL RULES:
    1. MAX WORD COUNT: 110 words. Do not exceed this. (Mandatory for retention).
    2. COLD OPEN: The first sentence must be a hook under 7 words.
    3. NO FILLER: Jump straight into the horror.
    
    Structure as valid JSON only:
    {{
        "title": "Clickbait Title (ALL CAPS)",
        "viral_comment": "Controversial question",
        "segments": [
            {{
                "text": "The Hook (Shocking statement)...",
                "image_prompt": "Terrifying, high contrast, hyper-detailed close-up shot of {topic}, 8k"
            }},
            {{
                "text": "The Mystery (Fast paced)...",
                "image_prompt": "Cinematic wide shot of..."
            }},
            {{
                "text": "The Twist (Leave them scared)...",
                "image_prompt": "Abstract horror art of..."
            }}
        ]
    }}
    """

    print(f"🧠 Brainstorming: {topic}...")
    
    try:
        response_text = generate_with_fallback(prompt)
        
        # Clean JSON
        clean_text = response_text.replace('```json', '').replace('```', '')
        data = json.loads(clean_text)
        
        safe_name = sanitize_filename(topic)
        filename = f"{safe_name}.json"
        
        with open(filename, 'w') as f:
            json.dump(data, f, indent=4)
            
        return filename
        
    except Exception as e:
        print(f"❌ Script Error: {e}")
        return None
