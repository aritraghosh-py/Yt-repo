import requests
import json
import os
import random
import time
import shutil

def download_free_images(json_filename):
    # 1. Load the script data
    try:
        with open(json_filename, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: Could not find file {json_filename}")
        return

    folder_name = json_filename.replace(".json", "")
    os.makedirs(f"assets/{folder_name}", exist_ok=True)
    
    saved_paths = []
    
    print(f"🎨 Downloading {len(data['segments'])} images (High-Fidelity Mode)...")
    print("⏳ This involves heavy rendering. Please be patient.")
    
    # --- THE "ECHOES OF REALITY" STYLE GUIDE ---
    # This ensures every image looks like it belongs to the same high-budget movie.
    style_modifiers = (
        "hyperrealistic, unreal engine 5 render, 8k resolution, "
        "cinematic lighting, volumetric fog, deep black and cyan color palette, "
        "glowing details, ominous atmosphere, rule of thirds composition, "
        "highly detailed texture, NO cartoon, NO anime, NO text"
    )

    # 2. Sequential Download Loop (Safe Mode to prevent crashing)
    for i, segment in enumerate(data['segments']):
        raw_prompt = segment['image_prompt']
        
        # Merge the AI's idea with our Brand Style
        final_prompt = f"{raw_prompt}, {style_modifiers}"
        
        # Add random seed to avoid caching
        seed = random.randint(1, 999999)
        
        # Use 'flux' model for best realism
        image_url = f"https://image.pollinations.ai/prompt/{final_prompt}?seed={seed}&width=720&height=1280&nologo=true&model=flux"
        path = f"assets/{folder_name}/image_{i}.jpg"
        
        success = False
        
        # --- RETRY LOOP (3 Attempts) ---
        for attempt in range(3):
            try:
                print(f"   ⬇️  Downloading Image {i+1} (Attempt {attempt+1})...")
                
                # Pretend to be a browser to avoid blocks
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                }
                
                # 60s timeout because 8k images take time
                response = requests.get(image_url, headers=headers, timeout=60)
                
                if response.status_code == 200:
                    with open(path, 'wb') as file:
                        file.write(response.content)
                    saved_paths.append(path)
                    print(f"      ✅ Success!")
                    success = True
                    break
                else:
                    print(f"      ⚠️ Server Error ({response.status_code}). Retrying...")
                    time.sleep(3)
                    
            except Exception as e:
                print(f"      ⚠️ Connection Issue: {e}")
                time.sleep(3)
        
        # --- FALLBACK (Safety Net) ---
        if not success:
            print(f"      ❌ Failed to generate Image {i+1}. Using fallback.")
            current_path = f"assets/{folder_name}/image_{i}.jpg"
            
            # If we have a previous image, duplicate it (Better than black screen)
            if len(saved_paths) > 0:
                shutil.copy(saved_paths[-1], current_path)
                saved_paths.append(current_path)
                print("      ↪️ Duplicated previous image.")
            else:
                # If the VERY first image fails, make a black placeholder
                from PIL import Image
                img = Image.new('RGB', (720, 1280), color='black')
                img.save(current_path)
                saved_paths.append(current_path)
                print("      ⬛ Created black placeholder.")
        
        # Polite sleep to prevent IP ban
        time.sleep(2)

    # 3. Save paths back to JSON
    data['image_paths'] = saved_paths
    with open(json_filename, 'w') as f:
        json.dump(data, f, indent=4)
        
    print(f"✅ Art Generation Complete.")

if __name__ == "__main__":
    pass
