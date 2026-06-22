# Install requirements before running:
# pip install pyTelegramBotAPI yt-dlp requests

import os
import re
import glob
import threading
import http.server
import socketserver
import http.cookiejar
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
import yt_dlp
import html
import io
import shutil

# ==========================================
# 1. SERVER & ENVIRONMENT SETUP
# ==========================================
def run_dummy_server():
    PORT = int(os.environ.get("PORT", 8080))
    class MyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot is active & listening.")
        def log_message(self, format, *args): pass 
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("0.0.0.0", PORT), MyHandler) as httpd:
            print(f"✅ Web server listening on port {PORT}")
            httpd.serve_forever()
    except Exception as e:
        print(f"⚠️ Web server failed to start: {e}")

threading.Thread(target=run_dummy_server, daemon=True).start()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
INSTAGRAM_COOKIES = os.environ.get("INSTAGRAM_COOKIES", "")
COOKIE_FILE = "/tmp/ig_cookies.txt"

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is not set in environment variables!")

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# 2. COOKIE GENERATION FOR REQUESTS & YT-DLP
# ==========================================
def prepare_cookies():
    if not INSTAGRAM_COOKIES:
        return False
    content = INSTAGRAM_COOKIES.strip()
    if not content.startswith("# Netscape HTTP Cookie File"):
        content = "# Netscape HTTP Cookie File\n" + content
    with open(COOKIE_FILE, "w") as f:
        f.write(content)
    return True

prepare_cookies()

def get_session_with_cookies():
    """Injects cookies into a standard requests session for fast API calls"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-IG-App-ID": "936619743392459" # Secret ID that bypasses web blocks
    })
    if os.path.exists(COOKIE_FILE):
        try:
            cj = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
            cj.load(ignore_discard=True, ignore_expires=True)
            session.cookies.update(cj)
        except Exception as e:
            print(f"⚠️ Cookie load failed: {e}")
    return session

# ==========================================
# 3. CORE LOGIC & YT-DLP INTEGRATION
# ==========================================
def parse_instagram_input(text):
    text = text.strip().lower()
    if text.startswith("@") and len(text) > 1:
        return {"type": "profile", "username": text[1:]}
    
    if "instagram.com" in text:
        clean_url = text.split("?")[0].rstrip("/")
        if re.search(r"instagram\.com/(p|reel|reels|tv|stories/highlights)/([^/]+)", clean_url) or \
           re.search(r"instagram\.com/stories/([^/]+)/([0-9]+)", clean_url):
            return {"type": "single", "url": clean_url}
        profile_match = re.search(r"instagram\.com/([^/]+)", clean_url)
        if profile_match:
            username = profile_match.group(1)
            if username not in ["explore", "about", "developer", "legal", "terms", "privacy", "reels"]:
                return {"type": "profile", "username": username}
                
    if " " not in text and "/" not in text and not text.startswith("http"):
        return {"type": "profile", "username": text}
    return None

def fetch_profile_data(username):
    """Fetches Bio, Avatar, and Status using API, falls back to yt-dlp metadata."""
    session = get_session_with_cookies()
    
    # METHOD 1: Web JSON API (Lightning Fast)
    try:
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        res = session.get(url, timeout=5)
        if res.status_code == 200:
            user = res.json().get('data', {}).get('user')
            if user:
                return {
                    "success": True,
                    "username": user.get('username'),
                    "full_name": user.get('full_name', ''),
                    "biography": user.get('biography', ''),
                    "followers": user.get('edge_followed_by', {}).get('count', 0),
                    "following": user.get('edge_follow', {}).get('count', 0),
                    "is_private": user.get('is_private', False),
                    "profile_pic_url": user.get('profile_pic_url_hd') or user.get('profile_pic_url')
                }
    except Exception:
        pass

    # METHOD 2: yt-dlp Native Extraction Fallback
    try:
        ydl_opts = {
            'cookiefile': COOKIE_FILE if os.path.exists(COOKIE_FILE) else None,
            'quiet': True,
            'extract_flat': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.instagram.com/{username}/", download=False)
            return {
                "success": True,
                "username": username,
                "full_name": info.get('title', username),
                "biography": info.get('description', 'Bio unavailable.'),
                "followers": 0, "following": 0,
                "is_private": False, 
                "profile_pic_url": info.get('thumbnails', [{}])[0].get('url') if info.get('thumbnails') else None
            }
    except Exception as e:
        return {"success": False, "error": f"Instagram blocked the request. Add Cookies. Error: {e}"}

def download_and_send(chat_id, status_msg_id, url, media_type, max_items=None):
    download_dir = f"/tmp/dl_{chat_id}_{status_msg_id}"
    os.makedirs(download_dir, exist_ok=True)

    ydl_opts = {
        'outtmpl': f'{download_dir}/%(playlist_index)s_%(id)s.%(ext)s',
        'cookiefile': COOKIE_FILE if os.path.exists(COOKIE_FILE) else None,
        'quiet': True,
        'no_warnings': True,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
    }
    
    if max_items:
        ydl_opts['playlistend'] = max_items

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        valid_exts = {'.mp4', '.mkv', '.avi', '.mov', '.jpg', '.jpeg', '.png', '.webp'}
        files = [f for f in glob.glob(f"{download_dir}/**/*", recursive=True) 
                 if os.path.isfile(f) and os.path.splitext(f)[1].lower() in valid_exts]
        
        # Sort so multi-part uploads stay in the correct order
        files.sort()

        if not files:
            bot.edit_message_text("❌ No media found. The content might be private.", chat_id, status_msg_id)
            return

        bot.edit_message_text(f"📤 Uploading {len(files)} {media_type}(s)...", chat_id, status_msg_id)

        media_group = []
        file_handles = []
        MAX_SIZE = 49 * 1024 * 1024 # Telegram File Limit

        for filepath in files:
            if os.path.getsize(filepath) > MAX_SIZE: continue 
            
            ext = os.path.splitext(filepath)[1].lower()
            f = open(filepath, 'rb')
            file_handles.append(f)
            
            if ext in ['.mp4', '.mkv', '.avi', '.mov']:
                media_group.append(InputMediaVideo(f))
            else:
                media_group.append(InputMediaPhoto(f))

        for i in range(0, len(media_group), 10):
            chunk = media_group[i:i+10]
            try:
                bot.send_media_group(chat_id, chunk)
            except Exception:
                for item in chunk:
                    item.media.seek(0)
                    try:
                        bot.send_video(chat_id, item.media) if isinstance(item, InputMediaVideo) else bot.send_photo(chat_id, item.media)
                    except Exception:
                        pass 

        for fh in file_handles: fh.close()
        bot.delete_message(chat_id, status_msg_id)

    except Exception as e:
        bot.edit_message_text(f"❌ Error extracting media: {str(e)[:100]}", chat_id, status_msg_id)
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)

# ==========================================
# 4. TELEGRAM BOT HANDLERS
# ==========================================
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    text = (
        "🤖 **Welcome to the Native Python Instagram Bot!**\n\n"
        "Send me any of the following:\n"
        "1️⃣ An Instagram **Username** (e.g. `cristiano`)\n"
        "2️⃣ A **Post/Reel/Story URL** directly\n"
        "3️⃣ A **Highlight URL** directly\n\n"
        "Powered natively by **yt-dlp** for ultra-fast speeds."
    )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    parsed = parse_instagram_input(message.text)
    
    if not parsed:
        bot.reply_to(message, "⚠️ Please send a valid Instagram URL or Username.")
        return

    # SINGLE MEDIA DOWNLOAD
    if parsed["type"] == "single":
        msg = bot.reply_to(message, "⏳ Extracting media using yt-dlp...")
        download_and_send(message.chat.id, msg.message_id, parsed["url"], "Media")
        
    # PROFILE MENU GENERATION
    elif parsed["type"] == "profile":
        username = parsed["username"]
        msg = bot.reply_to(message, f"🔍 Fetching profile data for **@{username}**...", parse_mode="Markdown")
        
        profile_data = fetch_profile_data(username)
        if not profile_data.get("success"):
            bot.edit_message_text(f"❌ {profile_data.get('error')}", message.chat.id, msg.message_id)
            return

        status = "🔒 Private Account" if profile_data["is_private"] else "🔓 Public Account"
        full_name_esc = html.escape(profile_data["full_name"] or "Unknown")
        username_esc = html.escape(profile_data["username"])
        bio_esc = html.escape(profile_data["biography"] or "No bio available.")
        followers_str = f"{profile_data['followers']:,}" if profile_data['followers'] > 0 else "N/A"
        following_str = f"{profile_data['following']:,}" if profile_data['following'] > 0 else "N/A"
        
        caption = (
            f"👤 <b>{full_name_esc}</b> (@{username_esc})\n"
            f"📊 {status}\n"
            f"👥 <b>Followers:</b> {followers_str} | <b>Following:</b> {following_str}\n\n"
            f"📝 <b>Bio:</b>\n{bio_esc}"
        )

        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("📸 Download HD Avatar", callback_data=f"ava:{profile_data['username']}"))
        markup.row(
            InlineKeyboardButton("🖼️ Last 10 Posts", callback_data=f"po:{profile_data['username']}"),
            InlineKeyboardButton("🎥 Last 10 Reels", callback_data=f"re:{profile_data['username']}")
        )
        markup.row(InlineKeyboardButton("⏱️ Active Stories", callback_data=f"st:{profile_data['username']}"))

        # Download Avatar to memory
        photo_data = None
        if profile_data.get("profile_pic_url"):
            try:
                r = requests.get(profile_data["profile_pic_url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if r.status_code == 200:
                    photo_data = io.BytesIO(r.content)
                    photo_data.name = "avatar.jpg"
            except Exception:
                pass

        bot.delete_message(message.chat.id, msg.message_id)
        try:
            if photo_data:
                bot.send_photo(message.chat.id, photo_data, caption=caption, parse_mode="HTML", reply_markup=markup)
            else:
                bot.send_message(message.chat.id, caption, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Telegram failed to format message: {e}")

# ==========================================
# 5. CALLBACK BUTTON INTERACTIONS
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    data = call.data
    chat_id = call.message.chat.id
    
    # 1. HD Avatar Fetcher
    if data.startswith("ava:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id, "Downloading HD Avatar...")
        try:
            profile = fetch_profile_data(username)
            if profile.get("success") and profile.get("profile_pic_url"):
                r = requests.get(profile["profile_pic_url"], timeout=10)
                photo_file = io.BytesIO(r.content)
                photo_file.name = f"{username}_avatar.jpg"
                bot.send_document(chat_id, photo_file)
                return
            bot.send_message(chat_id, "❌ Failed to fetch HD Avatar.")
        except Exception:
            bot.send_message(chat_id, "❌ Error retrieving avatar.")

    # 2. Extract Last 10 Posts
    elif data.startswith("po:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, f"⏳ Extracting last 10 posts for @{username}...")
        download_and_send(chat_id, msg.message_id, f"https://www.instagram.com/{username}/", "Posts", max_items=10)

    # 3. Extract Last 10 Reels
    elif data.startswith("re:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, f"⏳ Extracting last 10 reels for @{username}...")
        download_and_send(chat_id, msg.message_id, f"https://www.instagram.com/{username}/reels/", "Reels", max_items=10)

    # 4. Extract Stories
    elif data.startswith("st:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, f"⏳ Extracting active stories for @{username}...")
        download_and_send(chat_id, msg.message_id, f"https://www.instagram.com/stories/{username}/", "Stories")

print("🚀 Native yt-dlp Bot is active!")
bot.infinity_polling()
