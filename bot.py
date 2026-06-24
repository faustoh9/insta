import os
import shutil
import tempfile
import subprocess
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = "YOUR_BOT_TOKEN"


def download_instagram(url: str):
    temp_dir = tempfile.mkdtemp()

    try:
        cmd = [
            "gallery-dl",
            "-D",
            temp_dir,
            url,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise Exception(result.stderr)

        files = []

        for root, _, filenames in os.walk(temp_dir):
            for name in filenames:
                path = os.path.join(root, name)

                if os.path.isfile(path):
                    files.append(path)

        return files, temp_dir

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send an Instagram URL.\n\n"
        "Supported:\n"
        "- Posts\n"
        "- Reels\n"
        "- Stories (if accessible)\n"
        "- Profiles"
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "instagram.com" not in url:
        await update.message.reply_text(
            "Please send a valid Instagram URL."
        )
        return

    msg = await update.message.reply_text(
        "Downloading..."
    )

    temp_dir = None

    try:
        files, temp_dir = download_instagram(url)

        if not files:
            await msg.edit_text("No files found.")
            return

        await msg.edit_text(
            f"Downloaded {len(files)} file(s). Uploading..."
        )

        for file_path in files[:20]:

            size_mb = os.path.getsize(file_path) / (1024 * 1024)

            if size_mb > 49:
                continue

            ext = Path(file_path).suffix.lower()

            try:
                if ext in [".jpg", ".jpeg", ".png", ".webp"]:
                    with open(file_path, "rb") as f:
                        await update.message.reply_photo(f)

                elif ext in [".mp4", ".mov", ".mkv"]:
                    with open(file_path, "rb") as f:
                        await update.message.reply_video(f)

                else:
                    with open(file_path, "rb") as f:
                        await update.message.reply_document(f)

            except Exception:
                pass

        await msg.edit_text("Done.")

    except Exception as e:
        await msg.edit_text(
            f"Error:\n{e}"
        )

    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_url,
        )
    )

    print("Bot started...")

    app.run_polling()


if __name__ == "__main__":
    main()
