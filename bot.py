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
    """Formats and saves Netscape cookies for both gallery-dl and Instaloader"""
    if not INSTAGRAM_COOKIES:
        return False
    
    content = INSTAGRAM_COOKIES.strip()
    # Ensure standard Netscape header is present for compatibility
    if not content.startswith("# Netscape HTTP Cookie File"):
        content = "# Netscape HTTP Cookie File\n" + content
        
    with open(COOKIE_FILE, "w") as f:
        f.write(content)
    return True

prepare_cookies()

# Initialize Instaloader
L = instaloader.Instaloader(quiet=True)
if os.path.exists(COOKIE_FILE):
    try:
        cj = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
        cj.load(ignore_discard=True, ignore_expires=True)
        L.context._session.cookies.update(cj)
        # Inject CSRF token if available to prevent API blocks
        cookie_dict = L.context._session.cookies.get_dict()
        if 'csrftoken' in cookie_dict:
            L.context._session.headers.update({'X-CSRFToken': cookie_dict['csrftoken']})
        print("✅ Cookies injected into Instaloader successfully.")
    except Exception as e:
        print(f"⚠️ Instaloader cookie load failed: {e}")

# ==========================================
# 3. CORE LOGIC & DOWNLOADER
# ==========================================
def parse_instagram_input(text):
    text = text.strip()
    if text.startswith("@") and len(text) > 1:
        return {"type": "profile", "username": text[1:]}
    
    if "instagram.com" in text:
        clean_url = text.split("?")[0].rstrip("/")
        # Post/Reel/Story direct URL
        if re.search(r"instagram\.com/(p|reel|reels|tv)/([^/]+)", clean_url) or \
           re.search(r"instagram\.com/stories/([^/]+)/([0-9]+)", clean_url):
            return {"type": "single", "url": clean_url}
        # General Profile URL
        profile_match = re.search(r"instagram\.com/([^/]+)", clean_url)
        if profile_match:
            username = profile_match.group(1)
            if username not in ["explore", "about", "developer", "legal", "terms", "privacy", "reels"]:
                return {"type": "profile", "username": username}
                
    if " " not in text and len(text) > 0 and not text.startswith("http"):
        return {"type": "profile", "username": text}
    return None

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

        # Gather files
        valid_exts = {'.mp4', '.mkv', '.avi', '.mov', '.jpg', '.jpeg', '.png', '.webp'}
        files = [f for f in glob.glob(f"{download_dir}/**/*", recursive=True) 
                 if os.path.isfile(f) and os.path.splitext(f)[1].lower() in valid_exts]

        # Sort files by creation time so multi-part posts stay in order
        files.sort(key=os.path.getmtime)

        if not files:
            bot.edit_message_text("❌ No media found. It might be private or blocked.", chat_id, status_msg_id)
            return

        bot.edit_message_text(f"📤 Uploading {len(files)} {media_type}(s)...", chat_id, status_msg_id)

        media_group = []
        file_handles = []
        MAX_SIZE = 49 * 1024 * 1024 # 49 MB Telegram limit

        for filepath in files:
            if os.path.getsize(filepath) > MAX_SIZE:
                continue # Skip files too large for bot API
            
            ext = os.path.splitext(filepath)[1].lower()
            f = open(filepath, 'rb')
            file_handles.append(f)
            if ext in ['.mp4', '.mkv', '.avi', '.mov']:
                media_group.append(InputMediaVideo(f))
            else:
                media_group.append(InputMediaPhoto(f))

        # Upload in chunks of 10 (Telegram max album size)
        for i in range(0, len(media_group), 10):
            chunk = media_group[i:i+10]
            try:
                bot.send_media_group(chat_id, chunk)
            except Exception as e:
                # Fallback to single messages if grouping fails
                for item in chunk:
                    item.media.seek(0)
                    try:
                        if isinstance(item, InputMediaVideo):
                            bot.send_video(chat_id, item.media)
                        else:
                            bot.send_photo(chat_id, item.media)
                    except Exception:
                        pass 

        # Cleanup file handles
        for fh in file_handles:
            fh.close()

        bot.delete_message(chat_id, status_msg_id)

    except subprocess.TimeoutExpired:
        bot.edit_message_text("❌ Download timed out.", chat_id, status_msg_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", chat_id, status_msg_id)
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
        "3️⃣ A **Post/Reel/Story URL** directly\n\n"
        "I will fetch profiles, bios, highlights, and media in High Quality!"
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
        
    # PROFILE MENU
    elif parsed["type"] == "profile":
        username = parsed["username"]
        msg = bot.reply_to(message, f"🔍 Fetching profile data for **@{username}**...", parse_mode="Markdown")
        
        try:
            profile = instaloader.Profile.from_username(L.context, username)
        except instaloader.exceptions.ProfileNotExistsException:
            bot.edit_message_text("❌ Profile not found.", message.chat.id, msg.message_id)
            return
        except Exception as e:
            bot.edit_message_text("❌ Cannot fetch profile (Cookie might be invalid/expired or rate limit hit).", message.chat.id, msg.message_id)
            return

        # Prepare rich Profile UI
        status = "🔒 Private Account" if profile.is_private else "🔓 Public Account"
        caption = (
            f"👤 <b>{profile.full_name}</b> (@{profile.username})\n"
            f"📊 {status}\n"
            f"👥 <b>Followers:</b> {profile.followers:,} | <b>Following:</b> {profile.followees:,}\n\n"
            f"📝 <b>Bio:</b>\n{profile.biography}"
        )

        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("📸 Download HD Avatar", callback_data=f"ava:{username}"))
        
        if not profile.is_private or profile.followed_by_viewer:
            markup.row(
                InlineKeyboardButton("🖼️ Last 10 Posts", callback_data=f"po:{username}"),
                InlineKeyboardButton("🎥 Last 10 Reels", callback_data=f"re:{username}")
            )
            markup.row(
                InlineKeyboardButton("⏱️ All Stories", callback_data=f"st:{username}"),
                InlineKeyboardButton("✨ Highlights", callback_data=f"hl_menu:{username}")
            )

        bot.delete_message(message.chat.id, msg.message_id)
        bot.send_photo(
            message.chat.id,
            profile.profile_pic_url,
            caption=caption,
            parse_mode="HTML",
            reply_markup=markup
        )

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
            profile = instaloader.Profile.from_username(L.context, username)
            r = requests.get(profile.profile_pic_url, headers={"User-Agent": "Mozilla/5.0"})
            bot.send_document(chat_id, r.content, visible_file_name=f"{username}_avatar.jpg")
        except Exception:
            bot.send_message(chat_id, "❌ Failed to fetch HD Avatar.")

    # 2. Last 10 Posts
    elif data.startswith("po:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, f"⏳ Extracting last 10 posts for @{username}...")
        url = f"https://www.instagram.com/{username}/"
        download_and_send(chat_id, msg.message_id, url, "Posts", range_val="1-10")

    # 3. Last 10 Reels
    elif data.startswith("re:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, f"⏳ Extracting last 10 reels for @{username}...")
        url = f"https://www.instagram.com/{username}/reels/"
        download_and_send(chat_id, msg.message_id, url, "Reels", range_val="1-10")

    # 4. All Stories
    elif data.startswith("st:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, f"⏳ Extracting active stories for @{username}...")
        url = f"https://www.instagram.com/stories/{username}/"
        download_and_send(chat_id, msg.message_id, url, "Stories")

    # 5. Highlights Menu Fetcher
    elif data.startswith("hl_menu:"):
        username = data.split(":")[1]
        bot.answer_callback_query(call.id, "Loading Highlights...")
        
        try:
            profile = instaloader.Profile.from_username(L.context, username)
            highlights = list(instaloader.Highlight.all(L.context, profile))
        except Exception as e:
            bot.send_message(chat_id, "❌ Could not load highlights. (Login/Cookie required).")
            return

        if not highlights:
            bot.send_message(chat_id, f"ℹ️ No highlights found for @{username}.")
            return

        # Build interactive menu for highlights
        markup = InlineKeyboardMarkup()
        for h in highlights:
            title = h.title if h.title else "Untitled Highlight"
            markup.row(InlineKeyboardButton(f"✨ {title}", callback_data=f"dl_hl:{h.id}"))
            
        bot.send_message(chat_id, f"✨ Select a highlight to download from **@{username}**:", 
                         reply_markup=markup, parse_mode="Markdown")

    # 6. Download Specific Highlight
    elif data.startswith("dl_hl:"):
        hl_id = data.split(":")[1]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, "⏳ Extracting highlight stories...")
        url = f"https://www.instagram.com/stories/highlights/{hl_id}/"
        download_and_send(chat_id, msg.message_id, url, "Highlight")

print("🚀 Ultimate Telegram Bot is running...")
bot.infinity_polling()
