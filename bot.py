import os
import shutil
import tempfile
import subprocess
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable not found")


def download_instagram(url: str):
    """
    Download media using gallery-dl.
    Returns:
        files: list[str]
        temp_dir: str
    """

    temp_dir = tempfile.mkdtemp(prefix="instagram_")

    cmd = [
        "gallery-dl",
        "--directory",
        temp_dir,
        "--no-mtime",
        url,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise Exception(result.stderr.strip())

    files = []

    for root, _, filenames in os.walk(temp_dir):
        for filename in filenames:
            full_path = os.path.join(root, filename)

            if os.path.isfile(full_path):
                files.append(full_path)

    return files, temp_dir


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Instagram Downloader\n\n"
        "Send:\n"
        "- Post URL\n"
        "- Reel URL\n"
        "- Profile URL\n\n"
        "Examples:\n"
        "https://www.instagram.com/reel/xxxxx/\n"
        "https://www.instagram.com/p/xxxxx/\n"
        "https://www.instagram.com/username/"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send any Instagram URL and I will download it."
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text.strip()

    if not text:
        return

    if "instagram.com" not in text:
        await update.message.reply_text(
            "Please send a valid Instagram URL."
        )
        return

    status = await update.message.reply_text(
        "Downloading..."
    )

    temp_dir = None

    try:
        files, temp_dir = download_instagram(text)

        if not files:
            await status.edit_text(
                "Nothing found."
            )
            return

        await status.edit_text(
            f"Found {len(files)} file(s).\nUploading..."
        )

        sent = 0

        for file_path in files:

            try:
                size_mb = os.path.getsize(file_path) / (1024 * 1024)

                # Telegram bot limit safety
                if size_mb > 49:
                    continue

                suffix = Path(file_path).suffix.lower()

                if suffix in [
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                ]:
                    with open(file_path, "rb") as f:
                        await update.message.reply_photo(f)

                elif suffix in [
                    ".mp4",
                    ".mov",
                    ".mkv",
                ]:
                    with open(file_path, "rb") as f:
                        await update.message.reply_video(f)

                else:
                    with open(file_path, "rb") as f:
                        await update.message.reply_document(f)

                sent += 1

            except Exception:
                continue

        await status.edit_text(
            f"Done.\nSent {sent} file(s)."
        )

    except Exception as e:
        await status.edit_text(
            f"Error:\n{str(e)[:3500]}"
        )

    finally:
        if temp_dir:
            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    print("Bot started")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
