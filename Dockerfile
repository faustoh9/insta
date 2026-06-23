# Use official Python lightweight image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and force stdout to be unbuffered
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install FFmpeg (highly recommended for gallery-dl video processing)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file first to leverage Docker caching
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application files (bot.py, cookies.txt, etc.)
COPY . .

# Expose the port Render expects to bind to
EXPOSE 10000

# Command to run the bot
CMD ["python", "bot.py"]
