import os
import re
import logging
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
import instaloader
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables (Configured in Render dashboard)
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Optional: Add a free RapidAPI key if Instaloader gets IP-blocked
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "") 

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is missing!")

# Initialize Bot and Dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Initialize Instaloader (Disabled downloads to save memory and local disk space)
L = instaloader.Instaloader(
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False
)

# FastAPI App for Webhook handling
app = FastAPI()

# Extract Shortcode from Instagram Post/Reel URL
def extract_shortcode(url: str) -> str:
    match = re.search(r"/(?:p|reel|tv|reels)/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else None

# Extract Username from Profile URL or text
def extract_username(url: str) -> str:
    match = re.search(r"instagram\.com/([A-Za-z0-9_\.]+)", url)
    if match:
        username = match.group(1)
        if username not in ["p", "reel", "tv", "reels", "stories"]:
            return username
    # Handle simple plain text username input (e.g. 'cristiano')
    if re.match(r"^[A-Za-z0-9_\.]+$", url.strip()):
        return url.strip()
    return None

# Fallback: API Downloader via RapidAPI (Free tier)
async def download_via_rapidapi(message: types.Message, ig_url: str, status_msg: types.Message):
    # This uses a standard, fast RapidAPI endpoint structure
    api_url = "https://instagram-post-reels-stories-downloader.p.rapidapi.com/instagram"
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": "instagram-post-reels-stories-downloader.p.rapidapi.com"
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(api_url, headers=headers, params={"url": ig_url}, timeout=20.0)
            if response.status_code == 200:
                data = response.json()
                media_url = data.get("url") or data.get("links", [{}])[0].get("url")
                is_video = "mp4" in media_url or data.get("type") == "video"
                
                if media_url:
                    if is_video:
                        await message.answer_video(video=media_url)
                    else:
                        await message.answer_photo(photo=media_url)
                    await status_msg.delete()
                else:
                    await status_msg.edit_text("❌ Fallback API parsed data, but found no direct media link.")
            else:
                await status_msg.edit_text(f"❌ Fallback API error (HTTP {response.status_code}).")
        except Exception as e:
            await status_msg.edit_text(f"❌ Fallback API request failed: {str(e)}")

# Telegram Command: /start
@dp.message(commands=["start"])
async def send_welcome(message: types.Message):
    await message.answer(
        "👋 **Instagram Downloader Bot**\n\n"
        "• Send me an Instagram **Profile URL** (or username) to get their Bio & Profile Picture.\n"
        "• Send me a **Post or Reel link** to download the media directly!"
    )

# Handle incoming messages
@dp.message()
async def handle_message(message: types.Message):
    text = message.text
    if not text:
        return

    # 1. Profile / Bio Scraper
    username = extract_username(text)
    if username and "instagram.com" in text:
        status_msg = await message.answer("🔍 Fetching profile information...")
        try:
            profile = instaloader.Profile.from_username(L.context, username)
            bio_text = (
                f"👤 *Username:* @{profile.username}\n"
                f"📛 *Full Name:* {profile.full_name}\n"
                f"📝 *Bio:* {profile.biography if profile.biography else 'No bio'}\n"
                f"👥 *Followers:* {profile.followers:,}\n"
                f"📈 *Following:* {profile.followees:,}\n"
                f"📮 *Posts Count:* {profile.mediacount}"
            )
            await message.answer_photo(photo=profile.profile_pic_url, caption=bio_text, parse_mode="Markdown")
            await status_msg.delete()
        except Exception as e:
            await status_msg.edit_text(f"❌ Failed to fetch profile details: {str(e)}")
        return

    # 2. Post / Reel Media Downloader
    shortcode = extract_shortcode(text)
    if shortcode:
        status_msg = await message.answer("⚡ Extracting media link...")
        try:
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            caption = post.caption[:1024] if post.caption else None
            
            if post.is_video:
                await message.answer_video(video=post.video_url, caption=caption)
            else:
                await message.answer_photo(photo=post.url, caption=caption)
            
            await status_msg.delete()
        except Exception as e:
            logger.error(f"Instaloader failed (possibly rate-limited): {e}")
            if RAPIDAPI_KEY:
                await status_msg.edit_text("🔄 Free scraper rate-limited. Activating fallback API...")
                await download_via_rapidapi(message, text, status_msg)
            else:
                await status_msg.edit_text(
                    f"❌ Free extraction failed (Instagram Blocked the Request).\n"
                    f"To fix this, add a `RAPIDAPI_KEY` in your Render Environment Variables."
                )
        return

    await message.answer("👋 Please send a valid Instagram link (Profile, Post, or Reel).")

# Webhook Endpoints for Render
@app.on_event("startup")
async def on_startup():
    render_url = os.getenv("RENDER_EXTERNAL_URL")  # Render supplies this automatically
    if render_url:
        webhook_url = f"{render_url}/webhook"
        logger.info(f"Setting webhook to: {webhook_url}")
        await bot.set_webhook(url=webhook_url)
    else:
        logger.warning("RENDER_EXTERNAL_URL not found. Webhook not set.")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = types.Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "alive", "message": "Bot is running on Webhook mode."}
