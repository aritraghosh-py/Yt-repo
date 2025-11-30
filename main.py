import asyncio
import os
import sys
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

# Google & YouTube Imports
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from creator import generate_free_script, get_viral_topic
from free_artist import download_free_images
from free_audio import generate_voiceover
from editor import create_video

def post_comment(youtube, video_id, text):
    try:
        print(f"   💬 Posting Comment: {text}")
        youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": text
                        }
                    }
                }
            }
        ).execute()
        print("   ✅ Comment Posted!")
    except Exception as e:
        print(f"   ⚠️ Failed to post comment: {e}")

def upload_and_comment(video_path, title, topic, viral_comment):
    print("🚀 Uploading to YouTube...")
    
    token_json = os.environ.get('YOUTUBE_TOKEN')
    if not token_json:
        print("❌ No Token found.")
        return

    try:
        creds = Credentials.from_authorized_user_info(json.loads(token_json))
        youtube = build('youtube', 'v3', credentials=creds)
        
        # 1. Upload Video
        description_text = (
            f"The truth about {topic}.\n\n"
            "Echoes of Reality explores the glitches in our world, the paradoxes that break logic, "
            "and the dark corners of history.\n\n"
            "👁️ Subscribe to the Archive: https://youtube.com/@EchoesOfRealityShorts?sub_confirmation=1\n\n"
            "Questions asked in this video:\n"
            "• Is this real?\n"
            "• What are they hiding?\n\n"
            "📝 Note: This content is created with the assistance of AI for educational and storytelling purposes.\n"
            "#shorts #mystery #documentary #scifi #paradox"
        )

        request_body = {
            'snippet': {
                'title': f"{title} #Shorts",
                'description': description_text,
                'tags': ['shorts', 'mystery', 'paradox'],
                'categoryId': '27'
            },
            'status': {
                'privacyStatus': 'public', 
                'selfDeclaredMadeForKids': False
            }
        }
        
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part="snippet,status",
            body=request_body,
            media_body=media
        )
        response = request.execute()
        video_id = response['id']
        print(f"✅ UPLOAD COMPLETE! Video ID: {video_id}")
        
        # 2. Post Comment (Wait 5s for YouTube to process)
        print("   ⏳ Waiting for API to register video...")
        time.sleep(5)
        post_comment(youtube, video_id, viral_comment)

    except Exception as e:
        print(f"❌ Upload/Comment Failed: {e}")

def run_once():
    print("☁️ ECHOES OF REALITY: CLOUD BOT STARTED ☁️")
    
    # 1. Brain
    topic = get_viral_topic()
    json_filename = generate_free_script(topic)
    if not json_filename: sys.exit(1)
    
    # Load Data
    with open(json_filename, 'r') as f:
        data = json.load(f)
        video_title = data.get('title', topic)
        viral_comment = data.get('viral_comment', f"What do you think about {topic}? 👇")

    # 2. Assets
    try:
        asyncio.run(generate_voiceover(json_filename))
    except:
        sys.exit(1)

    download_free_images(json_filename)

    # 3. Editor
    try:
        create_video(json_filename)
        final_file = json_filename.replace('.json', '_FINAL.mp4')
    except:
        sys.exit(1)
    
    # 4. Upload & Comment
    if os.path.exists(final_file):
        upload_and_comment(final_file, video_title, topic, viral_comment)
    else:
        sys.exit(1)

if __name__ == "__main__":
    run_once()
    


