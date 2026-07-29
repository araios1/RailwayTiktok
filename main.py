import os
import time
import asyncio
import aiohttp
import uuid
import subprocess
import logging
from flask import Flask, jsonify
from threading import Thread
from shazamio import Shazam

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InputMediaPhoto

# --- ١. ڕێکخستنی سێرڤەری Keep-Alive بۆ Replit ---
flask_app = Flask('')

@flask_app.route('/')
def home():
    return jsonify({"status": "ok", "bot": "Replit TikTok Bot (API Only)"}), 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port, threaded=True)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()

# --- ٢. ڕێکخستنی بۆت ---
logging.basicConfig(level=logging.ERROR)
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    print("❌ هەڵە: تکایە BOT_TOKEN لە بەشی Secrets لە Replit دابنێ!")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

http_session = None

async def get_http_session():
    global http_session
    if http_session is None or http_session.closed:
        http_session = aiohttp.ClientSession()
    return http_session

# --- ٣. FFmpeg Helpers ---
async def extract_audio_fast(input_file_or_url, output_mp3):
    try:
        cmd = [
            'ffmpeg', '-y',
            '-threads', '0',
            '-i', input_file_or_url,
            '-vn',
            '-c:a', 'libmp3lame',
            '-q:a', '4',
            output_mp3
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await proc.wait()
        return os.path.exists(output_mp3) and os.path.getsize(output_mp3) > 5000
    except Exception:
        return False

async def extract_shazam_sample(input_file, output_sample):
    try:
        cmd = [
            'ffmpeg', '-y',
            '-threads', '0',
            '-ss', '00:00:03',
            '-i', input_file,
            '-t', '10',
            '-vn', '-ac', '1', '-ar', '22050',
            output_sample
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await proc.wait()
        return os.path.exists(output_sample)
    except Exception:
        return False

async def recognize_song(filepath):
    sample_path = f"sample_{uuid.uuid4()}.wav"
    try:
        shazam = Shazam()
        has_sample = await extract_shazam_sample(filepath, sample_path)
        target_file = sample_path if has_sample else filepath
        out = await shazam.recognize(target_file) if hasattr(shazam, 'recognize') else await shazam.recognize_song(target_file)

        track = out.get('track', {})
        if track:
            title = track.get('title', '')
            artist = track.get('subtitle', '')
            if title and artist: return f"{title} - {artist}"
            elif title: return title
        return None
    except Exception:
        return None
    finally:
        if os.path.exists(sample_path):
            try: os.remove(sample_path)
            except: pass

# --- ٤. داگرتنی تیکتۆک ---
async def download_tiktok(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    api_url = f"https://www.tikwm.com/api/?url={url}"
    try:
        session = await get_http_session()
        async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("code") == 0:
                    data = res["data"]
                    is_photo = "images" in data
                    info = {
                        "author": data["author"]["nickname"],
                        "likes": data["digg_count"],
                        "views": data["play_count"],
                        "is_photo": is_photo
                    }
                    media_links = data["images"] if is_photo else (data.get("hdplay") or data.get("play"))
                    return media_links, data.get("music"), info
        return None, None, None
    except Exception:
        return None, None, None

# --- ٥. فەرمانەکانی بۆت ---
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.reply("👋🏼 من دەتوانم ڤیدیۆ و وێنەی TikTok دابەزێنم.
🔗 تەنها لینکی تیکتۆک بنێرە:")

@dp.message(F.text)
async def handle_links(message: types.Message):
    url = message.text.strip()

    if "tiktok.com" in url or "douyin.com" in url:
        wait_msg = await message.reply("⏳ **خەریکی دابەزاندنم...**", parse_mode="Markdown")
        media_data, audio_url, info = await download_tiktok(url)

        if media_data:
            temp_raw = None
            temp_clean = None
            try:
                await wait_msg.edit_text("🔍 **خەریکی دۆزینەوەی ناوی گۆرانیەکە و ناردنەوەی پۆستەکەم...**", parse_mode="Markdown")
                temp_clean = f"clean_{uuid.uuid4()}.mp3"
                audio_ready = False

                if audio_url:
                    temp_raw = f"raw_{uuid.uuid4()}.tmp"
                    try:
                        session = await get_http_session()
                        async with session.get(audio_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=8)) as r:
                            if r.status == 200:
                                content = await r.read()
                                if len(content) > 5000:
                                    with open(temp_raw, 'wb') as f: f.write(content)
                                    audio_ready = await extract_audio_fast(temp_raw, temp_clean)
                    except Exception:
                        pass

                if not audio_ready and not info["is_photo"] and media_data:
                    audio_ready = await extract_audio_fast(media_data, temp_clean)

                song_name = None
                if audio_ready and os.path.exists(temp_clean):
                    song_name = await recognize_song(temp_clean)

                caption = f"👤 : {info['author']}
❤️ : {info['likes']} | 👀 : {info['views']}"
                if song_name:
                    caption += f"\n\n🎵 ناوی گۆرانی :\n**{song_name}**"
                caption += "\n\n⚙️Developer Bot @bu404"

                if info["is_photo"]:
                    images = media_data if isinstance(media_data, list) else [media_data]
                    for i in range(0, len(images), 10):
                        chunk = images[i:i + 10]
                        if len(chunk) == 1:
                            await bot.send_photo(chat_id=message.chat.id, photo=chunk[0], caption=caption if i == 0 else "", parse_mode="Markdown")
                        else:
                            media_group = [InputMediaPhoto(media=img, caption=caption if idx==0 and i==0 else "", parse_mode="Markdown") for idx, img in enumerate(chunk)]
                            await bot.send_media_group(chat_id=message.chat.id, media=media_group)
                        await asyncio.sleep(1)
                else:
                    video_input = media_data if isinstance(media_data, str) and media_data.startswith("http") else FSInputFile(media_data)
                    await bot.send_video(chat_id=message.chat.id, video=video_input, caption=caption, parse_mode="Markdown")

                await wait_msg.delete()
            except Exception as e:
                print(f"Error in TikTok flow: {e}")
            finally:
                for path in [temp_raw, temp_clean]:
                    if path and os.path.exists(path):
                        try: os.remove(path)
                        except: pass
        else:
            await wait_msg.edit_text("❌ نەمتوانی ڤیدیۆکە دابەزێنم.")
    else:
        await message.reply("❌ تکایە تەنها لینکی TikTok بنێرە.")

# --- ٦. دەستپێکردنی سێرڤەر و بۆت ---
async def main():
    keep_alive()
    print("🤖 بۆتەکە بە سەرکەوتوویی دەستی پێکرد...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())