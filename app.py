import telebot
import subprocess
import os
import glob
import shutil

# Get Token from Environment Variable
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is missing!")

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Welcome! Send me an Instagram Link (Profile, Post, Reel) and I will download it for you.")

@bot.message_handler(func=lambda message: True)
def handle_link(message):
    url = message.text.strip()
    
    if 'instagram.com' not in url:
        bot.reply_to(message, "Please send a valid Instagram link.")
        return

    # Send a waiting message
    status_msg = bot.reply_to(message, "⏳ Downloading... Please wait.")
    
    # Create a unique directory for this specific download
    dl_dir = f"./downloads/{message.chat.id}_{message.message_id}"
    os.makedirs(dl_dir, exist_ok=True)
    
    # Run gallery-dl command using subprocess
    # Note: --cookies is required for Instagram
    command = [
        'gallery-dl', 
        '--dest', dl_dir, 
        '--cookies', 'cookies.txt', 
        url
    ]
    
    try:
        # Execute the command
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Find downloaded files
        files = glob.glob(f"{dl_dir}/**/*", recursive=True)
        media_files = [f for f in files if os.path.isfile(f)]
        
        if not media_files:
            bot.edit_message_text("❌ Could not download anything. The profile might be private, or the link is invalid.", 
                                  chat_id=message.chat.id, message_id=status_msg.message_id)
            return

        bot.edit_message_text("📤 Uploading to Telegram...", 
                              chat_id=message.chat.id, message_id=status_msg.message_id)
        
        # Send files to the user
        for file_path in media_files:
            with open(file_path, 'rb') as f:
                if file_path.lower().endswith(('.mp4', '.webm')):
                    bot.send_video(message.chat.id, f)
                elif file_path.lower().endswith(('.jpg', '.jpeg', '.png')):
                    bot.send_photo(message.chat.id, f)
                else:
                    bot.send_document(message.chat.id, f)
                    
        # Delete the "Uploading..." message
        bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)

    except subprocess.CalledProcessError as e:
        error_output = e.stderr.decode('utf-8')
        bot.edit_message_text(f"❌ Error downloading: Account might be rate-limited or cookies expired.\n\nLogs: {error_output[:100]}", 
                              chat_id=message.chat.id, message_id=status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ An unexpected error occurred: {str(e)}", 
                              chat_id=message.chat.id, message_id=status_msg.message_id)
    finally:
        # Cleanup: Delete the downloaded files from the server to save space
        if os.path.exists(dl_dir):
            shutil.rmtree(dl_dir)

print("Bot is running...")
bot.infinity_polling()
