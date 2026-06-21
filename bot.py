import os
import re
import subprocess
import glob
import threading
import http.server
import socketserver
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo

# 1. Start a dummy web server on the port Render dynamically assigns
def run_dummy_server():
    PORT = int(os.environ.get("PORT", 8080))
    
    class MyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot is active.")

        def log_message(self, format, *args):
            pass  # Suppress logging spam

    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("0.0.0.0", PORT), MyHandler) as httpd:
            print(f"Render dummy web server listening on port {PORT}")
            httpd.serve_forever()
    except Exception as e:
        print(f"Web server failed to start: {e}")

threading.Thread(target=run_dummy_server, daemon=True).start()

# 2. Retrieve environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
INSTAGRAM_COOKIES = os.environ.get("INSTAGRAM_COOKIES")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set!")

bot = telebot.TeleBot(BOT_TOKEN)

# Helper function to classify and parse inputs
def parse_instagram_input(text):
    text = text.strip()
    
    # Check for @username format
    if text.startswith("@") and len(text) > 1:
        return {"type": "profile", "username": text[1:]}
    
    # Check for Instagram URL
    if "instagram.com" in text:
        clean_url = text.split("?")[0].rstrip("/")
        
        # Match single post/reel/tv formats:
        # e.g., instagram.com/p/SHORTCODE, instagram.com/reel/SHORTCODE
        single_post_pattern = re.compile(r"instagram\.com/(p|reel|reels|tv)/([^/]+)")
        if single_post_pattern.search(clean_url):
            return {"type": "single", "url": clean_url}
            
        # Match story formats with specific story ID:
        # e.g., instagram.com/stories/username/STORY_ID
        story_pattern = re.compile(r"instagram\.com/stories/([^/]+)/([0-9]+)")
        if story_pattern.search(clean_url):
            return {"type": "single", "url": clean_url}
            
        # Match general profile url: instagram.com/username
        profile_pattern = re.compile(r"instagram\.com/([^/]+)")
        profile_match = profile_pattern.search(clean_url)
        if profile_match:
            username = profile_match.group(1)
            excluded_routes = ["explore", "about", "developer", "legal", "terms", "privacy", "directory", "accounts"]
            if username not in excluded_routes:
                return {"type": "profile", "username": username}
                
    # If it is a raw username with no spaces or URL structure
    if " " not in text and len(text) > 0 and not text.startswith("http"):
        return {"type": "profile", "username": text}
        
    return None

# Unified function to download and send media
def download_and_send(chat_id, status_msg_id, url, media_type, range_val=None):
    download_dir = f"/tmp/dl_{chat_id}_{status_msg_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        cmd = ["gallery-dl", "--directory", download_dir]
        
        # Inject cookie file if configured
        cookie_filepath = "/tmp/ig_cookies.txt"
        if INSTAGRAM_COOKIES:
            with open(cookie_filepath, "w") as f:
                f.write(INSTAGRAM_COOKIES.strip())
            cmd.extend(["--cookies", cookie_filepath])

        # Inject download limits (e.g., 1-10)
        if range_val:
            cmd.extend(["--range", range_val])

        cmd.append(url)

        # Run extraction
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180  # 3 minutes maximum for batch downloads
        )

        if result.stdout:
            print(f"gallery-dl output:\n{result.stdout}")
        if result.stderr:
            print(f"gallery-dl logs/errors:\n{result.stderr}")

        if result.returncode != 0:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"Failed to retrieve {media_type}. Ensure the account is public (or you have configured active burner cookies)."
            )
            return

        # Find downloaded files
        downloaded_files = glob.glob(f"{download_dir}/**/*", recursive=True)
        valid_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.jpg', '.jpeg', '.png', '.webp', '.gif']
        files_to_send = [
            f for f in downloaded_files 
            if os.path.isfile(f) and os.path.splitext(f)[1].lower() in valid_extensions
        ]

        if not files_to_send:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"No {media_type} files were found."
            )
            return

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=f"📤 Uploading {len(files_to_send)} file(s)..."
        )

        # Assemble files into a structured media group (albums of up to 10 files)
        media_group = []
        file_handles = []
        for filepath in files_to_send:
            ext = os.path.splitext(filepath)[1].lower()
            f = open(filepath, 'rb')
            file_handles.append(f)
            if ext in ['.mp4', '.mkv', '.avi', '.mov']:
                media_group.append(InputMediaVideo(f))
            else:
                media_group.append(InputMediaPhoto(f))

        # Upload to Telegram in groups of 10
        for i in range(0, len(media_group), 10):
            chunk = media_group[i:i+10]
            try:
                bot.send_media_group(chat_id, chunk)
            except Exception as e:
                print(f"Media group failed, falling back to direct upload: {e}")
                # Fallback to individual sending if media grouping fails
                for item in chunk:
                    item.media.seek(0)
                    if isinstance(item, InputMediaVideo):
                        bot.send_video(chat_id, item.media)
                    else:
                        bot.send_photo(chat_id, item.media)

        # Close all open files
        for fh in file_handles:
            fh.close()

        # Delete progress message on completion
        bot.delete_message(chat_id, status_msg_id)

    except subprocess.TimeoutExpired:
        bot.edit_message_text(chat_id=chat_id, message_id=status_msg_id, text="The download process timed out.")
    except Exception as e:
        bot.edit_message_text(chat_id=chat_id, message_id=status_msg_id, text=f"An unexpected error occurred: {str(e)}")
    finally:
        subprocess.run(["rm", "-rf", download_dir])
        if os.path.exists("/tmp/ig_cookies.txt"):
            os.remove("/tmp/ig_cookies.txt")

# Handle incoming text messages
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    parsed = parse_instagram_input(message.text)
    
    if not parsed:
        bot.reply_to(message, "Please send a valid Instagram URL, @username, or username.")
        return

    # Direct Download routing
    if parsed["type"] == "single":
        status_message = bot.reply_to(message, "Extracting single media... Please wait.")
        download_and_send(
            chat_id=message.chat.id,
            status_msg_id=status_message.message_id,
            url=parsed["url"],
            media_type="Media"
        )
        
    # Interactive Menu routing
    elif parsed["type"] == "profile":
        username = parsed["username"]
        
        # Build inline keyboard buttons
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("👤 Profile Picture", callback_data=f"av:{username}"),
            InlineKeyboardButton("🎥 Last 10 Stories", callback_data=f"st:{username}")
        )
        markup.add(
            InlineKeyboardButton("🖼️ Last 10 Posts", callback_data=f"po:{username}")
        )
        
        bot.send_message(
            message.chat.id,
            f"👤 **Instagram Profile: @{username}**\nSelect what you would like to retrieve:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

# Handle button interactions
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    data = call.data
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    if data.startswith("av:"):
        username = data.split(":")[1]
        url = f"https://www.instagram.com/{username}/avatar"
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏳ Extracting avatar for @{username}..."
        )
        download_and_send(chat_id, message_id, url, "Profile Picture")
        
    elif data.startswith("st:"):
        username = data.split(":")[1]
        url = f"https://www.instagram.com/stories/{username}/"
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏳ Extracting up to 10 active stories for @{username}..."
        )
        download_and_send(chat_id, message_id, url, "Stories", range_val="1-10")
        
    elif data.startswith("po:"):
        username = data.split(":")[1]
        url = f"https://www.instagram.com/{username}/"
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏳ Extracting last 10 posts for @{username}..."
        )
        download_and_send(chat_id, message_id, url, "Posts", range_val="1-10")

print("Telegram Bot is running...")
bot.infinity_polling()
