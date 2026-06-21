import os
import subprocess
import glob
import threading
import http.server
import socketserver
import telebot

# 1. Start a dummy web server on the port Render dynamically assigns
def run_dummy_server():
    # Render automatically injects the PORT environment variable (usually 10000+)
    PORT = int(os.environ.get("PORT", 8080))
    
    class MyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # Health check endpoint for Render
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot is active.")

        def log_message(self, format, *args):
            pass  # Suppress logging spam in Render logs

    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("0.0.0.0", PORT), MyHandler) as httpd:
            print(f"Render dummy web server listening on port {PORT}")
            httpd.serve_forever()
    except Exception as e:
        print(f"Web server failed to start: {e}")

# Run the web server in a background thread so it doesn't block the bot
threading.Thread(target=run_dummy_server, daemon=True).start()

# 2. Retrieve environment secrets from Render
BOT_TOKEN = os.environ.get("BOT_TOKEN")
INSTAGRAM_COOKIES = os.environ.get("INSTAGRAM_COOKIES")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set!")

# Initialize Telegram Bot (connecting directly, no proxy needed)
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Hello from Render! Send me an Instagram link, and I will try to retrieve the media.")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    url = message.text.strip()
    
    if "instagram.com" not in url:
        bot.reply_to(message, "Please send a valid Instagram URL.")
        return

    status_message = bot.reply_to(message, "Extracting media... Please wait.")
    download_dir = f"/tmp/dl_{message.chat.id}_{message.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        cmd = ["gallery-dl", "--directory", download_dir]
        
        # Write cookies to a temporary file if provided in Render Environment
        cookie_filepath = "/tmp/ig_cookies.txt"
        if INSTAGRAM_COOKIES:
            with open(cookie_filepath, "w") as f:
                f.write(INSTAGRAM_COOKIES.strip())
            cmd.extend(["--cookies", cookie_filepath])

        cmd.append(url)

        # Run gallery-dl
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120
        )

        if result.stdout:
            print(f"gallery-dl output:\n{result.stdout}")
        if result.stderr:
            print(f"gallery-dl logs/errors:\n{result.stderr}")

        if result.returncode != 0:
            bot.edit_message_text(
                "Failed to retrieve media. This post might require a login, or Render's IP is being blocked.",
                message.chat.id, 
                status_message.message_id
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
            bot.edit_message_text("No media files were found in this link.", message.chat.id, status_message.message_id)
            return

        bot.edit_message_text(f"Uploading {len(files_to_send)} file(s)...", message.chat.id, status_message.message_id)

        for filepath in files_to_send:
            ext = os.path.splitext(filepath)[1].lower()
            with open(filepath, 'rb') as media:
                if ext in ['.mp4', '.mkv', '.avi', '.mov']:
                    bot.send_video(message.chat.id, media)
                else:
                    bot.send_photo(message.chat.id, media)

        bot.delete_message(message.chat.id, status_message.message_id)

    except subprocess.TimeoutExpired:
        bot.edit_message_text("The download process timed out.", message.chat.id, status_message.message_id)
    except Exception as e:
        bot.edit_message_text(f"An unexpected error occurred: {str(e)}", message.chat.id, status_message.message_id)
    finally:
        # Clean up files immediately
        subprocess.run(["rm", "-rf", download_dir])
        if os.path.exists("/tmp/ig_cookies.txt"):
            os.remove("/tmp/ig_cookies.txt")

print("Telegram Bot is running...")
bot.infinity_polling()
