import json
import os
import random
import textwrap

# Fix for PIL Image Antialias
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

from moviepy.editor import *
# --- THIS WAS MISSING BEFORE ---
import moviepy.audio.fx.all as afx 
import moviepy.video.fx.all as vfx
# -------------------------------

def create_video(json_filename):
    print("   🔹 Step 1: Loading Data...")
    try:
        with open(json_filename, 'r') as f:
            data = json.load(f)
            video_title = data.get('title', json_filename.replace('.json', '').replace('_', ' '))
    except FileNotFoundError:
        print(f"❌ Error: File {json_filename} not found.")
        return
        
    folder_name = json_filename.replace(".json", "")
    audio_path = f"assets/{folder_name}/voiceover.mp3"
    asset_dir = f"assets/{folder_name}"
    bg_music_path = "mystery_bg.mp3" 
    
    # 1. Load Images
    try:
        image_files = [f for f in os.listdir(asset_dir) if f.endswith('.jpg') or f.endswith('.png')]
        image_files.sort(key=lambda f: int(''.join(filter(str.isdigit, f))))
        image_paths = [os.path.join(asset_dir, f) for f in image_files]
        print(f"   🔹 Step 2: Found {len(image_paths)} images.")
    except Exception as e:
        print(f"❌ Error finding images: {e}")
        return
    
    if not os.path.exists(audio_path):
        print(f"❌ Audio missing at {audio_path}")
        return

    # 2. AUDIO SETUP
    print("   🔹 Step 3: Setting up Audio...")
    voice_clip = AudioFileClip(audio_path)
    
    # Check for Background Music (With Safety Net)
    final_audio = voice_clip # Default to just voice
    
    if os.path.exists(bg_music_path):
        try:
            print("      - Adding Background Music...")
            music_clip = AudioFileClip(bg_music_path)
            # Loop music to match voice length
            music_clip = afx.audio_loop(music_clip, duration=voice_clip.duration + 2)
            music_clip = music_clip.volumex(0.10)
            music_clip = music_clip.audio_fadeout(2)
            final_audio = CompositeAudioClip([voice_clip, music_clip])
        except Exception as e:
            print(f"      ⚠️ Music Failed: {e}. Skipping music.")
            final_audio = voice_clip
    else:
        print("      - No background music file found.")

    # 3. VISUAL SETUP
    print("   🔹 Step 4: Processing Images...")
    total_duration = voice_clip.duration
    duration_per_image = total_duration / len(image_paths)

    clips = []
    for img_path in image_paths:
        clip = ImageClip(img_path).set_duration(duration_per_image)
        clip = clip.resize(height=1280).set_position("center")
        
        # Ken Burns Zoom
        zoom_mode = random.choice(['in', 'out'])
        if zoom_mode == 'in':
            clip = clip.resize(lambda t: 1 + 0.04 * t)
        else:
            clip = clip.resize(lambda t: 1.10 - 0.04 * t)
        
        # Dark Filter
        clip = clip.fx(vfx.colorx, 0.9)
        clips.append(clip)

    base_video = concatenate_videoclips(clips, method="compose")
    base_video = base_video.set_audio(final_audio)
    
    # 4. TEXT OVERLAY
    print("   🔹 Step 5: Creating Text Overlay...")
    try:
        wrapped_title = "\n".join(textwrap.wrap(video_title.upper(), width=15))
        
        # Use 'Arial' or 'DejaVuSans' which is standard on Linux servers
        txt_clip = TextClip(
            wrapped_title,
            fontsize=80,
            color='white',
            font='DejaVu-Sans-Bold', # Safe font for Linux
            stroke_color='black',
            stroke_width=4,
            method='caption',
            align='center',
            size=(720, None)
        )
        
        txt_clip = txt_clip.set_position(('center', 300)).set_duration(3.5)
        txt_clip = txt_clip.crossfadein(0.5).crossfadeout(0.5)
        
        # Background Box
        bg_box = ColorClip(
            size=(720, int(txt_clip.h * 1.2)),
            color=(0,0,0)
        ).set_opacity(0.4).set_duration(3.5).set_position(('center', 300))
        bg_box = bg_box.crossfadein(0.5).crossfadeout(0.5)

        final_video = CompositeVideoClip([base_video, bg_box, txt_clip])
    except Exception as e:
        print(f"⚠️ Text Overlay Failed: {e}. Exporting without text.")
        final_video = base_video

    # 5. RENDER
    print("   🔹 Step 6: Rendering...")
    output_filename = f"{folder_name}_FINAL.mp4"
    
    final_video.write_videofile(
        output_filename, 
        fps=24, 
        codec="libx264", 
        audio_codec="aac",
        threads=4,
        preset="ultrafast"
    )
    
    print(f"✅ VIDEO SAVED: {output_filename}")

if __name__ == "__main__":
    pass
