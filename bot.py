import os
import subprocess
import shutil
import telebot
from flask import Flask
from threading import Thread

# Get the Bot Token from Render Environment Variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# Initialize the bot
bot = telebot.TeleBot(BOT_TOKEN)

# Flask app to satisfy Render.com port binding requirement
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running!"

def run_flask():
    # Render assigns a dynamic port, default to 10000 if not found
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋 Welcome! Send me any Instagram URL (profile, post, or reel) and I will download the media for you.")

@bot.message_handler(func=lambda message: 'instagram.com' in message.text)
def handle_instagram_url(message):
    url = message.text
    status_msg = bot.reply_to(message, "⏳ Downloading media... Please wait.")
    
    # Create a unique temporary directory for this specific download
    dl_dir = f"./downloads/{message.chat.id}_{message.message_id}"
    os.makedirs(dl_dir, exist_ok=True)
    
    try:
        # Build the gallery-dl command
        command = ['gallery-dl', '-d', dl_dir]
        
        # Attach cookies if the file exists (Highly recommended for Instagram)
        if os.path.exists('cookies.txt'):
            command.extend(['--cookies', 'cookies.txt'])
            
        command.append(url)
        
        # Run gallery-dl via command line
        subprocess.run(command, check=True)
        
        # Find all downloaded files in the directory
        downloaded_files = []
        for root, dirs, files in os.walk(dl_dir):
            for file in files:
                downloaded_files.append(os.path.join(root, file))
                
        if not downloaded_files:
            bot.edit_message_text("❌ Could not find any media. The account might be private, or the cookies have expired.", 
                                  chat_id=message.chat.id, 
                                  message_id=status_msg.message_id)
            return

        bot.edit_message_text("✅ Media downloaded! Uploading to Telegram...", 
                              chat_id=message.chat.id, 
                              message_id=status_msg.message_id)

        # Upload files back to the user
        for file_path in downloaded_files:
            with open(file_path, 'rb') as f:
                if file_path.endswith(('.mp4', '.mov')):
                    bot.send_video(message.chat.id, f)
                elif file_path.endswith(('.jpg', '.jpeg', '.png')):
                    bot.send_photo(message.chat.id, f)
                else:
                    bot.send_document(message.chat.id, f)
                    
        # Delete the status message once everything is sent
        bot.delete_message(message.chat.id, status_msg.message_id)
    
    except subprocess.CalledProcessError:
        bot.edit_message_text("❌ Error downloading from the provided URL. Check if the link is valid.", 
                              chat_id=message.chat.id, 
                              message_id=status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ An error occurred: {str(e)}", 
                              chat_id=message.chat.id, 
                              message_id=status_msg.message_id)
    finally:
        # Clean up: Delete the temporary files to save disk space on Render
        if os.path.exists(dl_dir):
            shutil.rmtree(dl_dir)

if __name__ == "__main__":
    # 1. Start the Flask web server in a background thread
    Thread(target=run_flask).start()
    
    # 2. Remove any existing webhooks to prevent 409 Conflict errors
    bot.remove_webhook()
    
    # 3. Start the Telegram bot polling
    print("Bot is starting...")
    bot.infinity_polling()
