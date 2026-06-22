import os
import re
import subprocess
import glob
import threading
import http.server
import socketserver
import http.cookiejar
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
import instaloader
import html
import io

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
# 2. UNIFIED AUTHENTICATION (Cookies)
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

L = instaloader.Instaloader(quiet=True)
if os.path.exists(COOKIE_FILE):
    try:
        cj = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
        cj.load(ignore_discard=True, ignore_expires=True)
        L.context._session.cookies.update(cj)
        cookie_dict = L.context._session.cookies.get_dict()
        if 'csrftoken' in cookie_dict:
            L.context._session.headers.update({'X-CSRFToken': cookie_dict['csrftoken']})
    except Exception as e:
        print(f"⚠️ Instaloader cookie load failed: {e}")

# ==========================================
# 3. CORE LOGIC & DOWNLOADER
# ==========================================
def parse_instagram_input(text):
    text = text.strip().lower() # Make it lowercase to avoid IG errors
    
    if text.startswith("@") and len(text) > 1:
        return {"type": "profile", "username": text[1:]}
    
    if "instagram.com" in text:
        clean_url = text.split("?")[0].rstrip("/")
        if re.search(r"instagram\.com/(p|reel|reels|tv)/([^/]+)", clean_url) or \
           re.search(r"instagram\.com/stories/([^/]+)/([0-9]+)", clean_url):
            return {"type": "single", "url": clean_url}
        profile_match = re.search(r"instagram\.com/([^/]+)", clean_url)
        if profile_match:
            username = profile_match.group(1)
            if username not in ["explore", "about", "developer", "legal", "terms", "privacy", "reels"]:
                return {"type": "profile", "username": username}
                
    # If it's a single word with no spaces and not a URL, it's a username!
    if " " not in text and "/" not in text and not text.startswith("http"):
        return {"type": "profile", "username": text}
    return None

def fetch_profile_data(username):
    """
    Highly Optimized Fetcher:
    Tries the fast official Instagram JSON API first (bypasses Instaloader limits).
    Falls back to Instaloader if the API is restricted.
    """
    # METHOD 1: Direct JSON API Request (Blazing Fast)
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-IG-App-ID": "936619743392459" # Magic ID that bypasses login walls for profiles
        }
        res = requests.get(f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}", headers=headers, timeout=5)
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
                    "profile_pic_url": user.get('profile_pic_url_hd'),
                    "followed_by_viewer": False
                }
    except Exception as e:
        print(f"API Method Failed, trying fallback... {e}")

    # METHOD 2: Instaloader Fallback
    try:
        profile = instaloader.Profile.from_username(L.context, username)
        return {
            "success": True,
            "username": profile.username,
            "full_name": profile.full_name,
            "biography": profile.biography,
            "followers": profile.followers,
            "following": profile.followees,
            "is_private": profile.is_private,
            "profile_pic_url": profile.profile_pic_url,
            "followed_by_viewer": profile.followed_by_viewer
        }
    except instaloader.exceptions.ProfileNotExistsException:
        return {"success": False, "error": "Profile not found or username is incorrect."}
    except Exception as e:
        return {"success": False, "error": f"Instagram blocked the request (Rate Limited). Add valid Cookies."}

def download_and_send(chat_id, status_msg_id, url, media_type, range_val=None):
    download_dir = f"/tmp/dl_{chat_id}_{status_msg_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        cmd = ["gallery-dl", "--directory", download_dir]
        if os.path.exists(COOKIE_FILE):
            cmd.extend(["--cookies", COOKIE_FILE])
        if range_val:
            cmd.extend(["--range", range_val])
        cmd.append(url)

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=240)

        valid_exts = {'.mp4', '.mkv', '.avi', '.mov', '.jpg', '.jpeg', '.png', '.webp'}
        files = [f for f in glob.glob(f"{download_dir}/**/*", recursive=True) 
                 if os.path.isfile(f) and os.path.splitext(f)[1].lower() in valid_exts]
        files.sort(key=os.path.getmtime)

        if not files:
            bot.edit_message_text("❌ No media found. The content might be private.", chat_id, status_msg_id)
            return

        bot.edit_message_text(f"📤 Uploading {len(files)} {media_type}(s)...", chat_id, status_msg_id)

        media_group = []
        file_handles = []
        MAX_SIZE = 49 * 1024 * 1024 

        for filepath in files:
            if os.path.getsize(filepath) > MAX_SIZE:
                continue 
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

        for fh in file_handles:
            fh.close()

        bot.delete_message(chat_id, status_msg_id)

    except Exception as e:
        bot.edit_message_text(f"❌ Error during download: {str(e)}", chat_id, status_msg_id)
    finally:
        subprocess.run(["rm", "-rf", download_dir])

# ==========================================
# 4. TELEGRAM BOT HANDLERS
# ==========================================
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    text = (
        "🤖 **Welcome to the Ultimate Instagram Bot!**\n\n"
        "Send me any of the following:\n"
        "1️⃣ An Instagram **Username** (e.g. `cristiano`)\n"
        "2️⃣ An Instagram **Profile URL**\n"
        "3️⃣ A **Post/Reel/Story URL** directly"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    parsed = parse_instagram_input(message.text)
    
    if not parsed:
        bot.reply_to(message, "⚠️ Please send a valid Instagram URL or Username.")
        return

    # DIRECT MEDIA LINK
    if parsed["type"] == "single":
        msg = bot.reply_to(message, "⏳ Extracting media... Please wait.")
        download_and_send(message.chat.id, msg.message_id, parsed["url"], "Media")
        
    # PROFILE MENU (Highly Optimized)
    elif parsed["type"] == "profile":
        username = parsed["username"]
        msg = bot.reply_to(message, f"🔍 Fetching profile data for **@{username}**...", parse_mode="Markdown")
        
        # 1. Grab Profile Data
        profile_data = fetch_profile_data(username)
        if not profile_data.get("success"):
            bot.edit_message_text(f"❌ {profile_data.get('error')}", message.chat.id, msg.message_id)
            return

        # 2. Format UI (With HTML escaping to prevent Telegram crashes)
        status = "🔒 Private Account" if profile_data["is_private"] else "🔓 Public Account"
        full_name_esc = html.escape(profile_data["full_name"] or "Unknown")
        username_esc = html.escape(profile_data["username"])
        bio_esc = html.escape(profile_data["biography"] or "No bio available.")
        
        caption = (
            f"👤 <b>{full_name_esc}</b> (@{username_esc})\n"
            f"📊 {status}\n"
            f"👥 <b>Followers:</b> {profile_data['followers']:,} | <b>Following:</b> {profile_data['following']:,}\n\n"
            f"📝 <b>Bio:</b>\n{bio_esc}"
        )

        # 3. Create interactive Buttons
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("📸 Download HD Avatar", callback_data=f"ava:{profile_data['username']}"))
        
        if not profile_data["is_private"] or profile_data.get("followed_by_viewer"):
            markup.row(
                InlineKeyboardButton("🖼️ Last 10 Posts", callback_data=f"po:{profile_data['username']}"),
                InlineKeyboardButton("🎥 Last 10 Reels", callback_data=f"re:{profile_data['username']}")
            )
            markup.row(
                InlineKeyboardButton("⏱️ All Stories", callback_data=f"st:{profile_data['username']}"),
                InlineKeyboardButton("✨ Highlights", callback_data=f"hl_menu:{profile_data['username']}")
            )

        # 4. Securely Download Avatar to Memory
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
        
        # 5. Send result to User
        try:
            if photo_data:
                bot.send_photo(message.chat.id, photo_data, caption=caption, parse_mode="HTML", reply_markup=markup)
            else:
                bot.send_message(message.chat.id, caption, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Telegram failed to render message formatting: {e}")

# ==========================================
# 5. CALLBACK / BUTTON INTERACTIONS
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    data = call.data
    chat_id = call.message.chat.id
    
    # 1. HD Avatar
    if data.startswith("ava:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id, "Downloading HD Avatar...")
        try:
            profile = fetch_profile_data(username)
            if profile.get("success") and profile.get("profile_pic_url"):
                r = requests.get(profile["profile_pic_url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if r.status_code == 200:
                    photo_file = io.BytesIO(r.content)
                    photo_file.name = f"{username}_hd_avatar.jpg"
                    bot.send_document(chat_id, photo_file)
                    return
            bot.send_message(chat_id, "❌ Failed to fetch HD Avatar.")
        except Exception:
            bot.send_message(chat_id, "❌ Error getting avatar.")

    # 2. Last 10 Posts
    elif data.startswith("po:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, f"⏳ Extracting last 10 posts for @{username}...")
        download_and_send(chat_id, msg.message_id, f"https://www.instagram.com/{username}/", "Posts", "1-10")

    # 3. Last 10 Reels
    elif data.startswith("re:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, f"⏳ Extracting last 10 reels for @{username}...")
        download_and_send(chat_id, msg.message_id, f"https://www.instagram.com/{username}/reels/", "Reels", "1-10")

    # 4. All Stories
    elif data.startswith("st:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, f"⏳ Extracting active stories for @{username}...")
        download_and_send(chat_id, msg.message_id, f"https://www.instagram.com/stories/{username}/", "Stories")

    # 5. Highlights Menu
    elif data.startswith("hl_menu:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id, "Loading Highlights...")
        try:
            profile = instaloader.Profile.from_username(L.context, username)
            highlights = list(instaloader.Highlight.all(L.context, profile))
        except Exception:
            bot.send_message(chat_id, "❌ Could not load highlights. (Login/Cookie required for highlights).")
            return
        if not highlights:
            bot.send_message(chat_id, f"ℹ️ No highlights found for @{username}.")
            return
        markup = InlineKeyboardMarkup()
        for h in highlights:
            title = h.title if h.title else "Untitled"
            markup.row(InlineKeyboardButton(f"✨ {title}", callback_data=f"dl_hl:{h.id}"))
        bot.send_message(chat_id, f"✨ Select a highlight from **@{username}**:", reply_markup=markup, parse_mode="Markdown")

    # 6. Download Highlight
    elif data.startswith("dl_hl:"):
        hl_id = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, "⏳ Extracting highlight...")
        download_and_send(chat_id, msg.message_id, f"https://www.instagram.com/stories/highlights/{hl_id}/", "Highlight")

print("🚀 Ultimate Telegram Bot is running...")
bot.infinity_polling()
