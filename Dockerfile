FROM python:3.11-slim

# Install ffmpeg (gallery-dl sometimes needs it for video processing)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirement file and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot code (now named app.py) and cookies
COPY app.py .
COPY cookies.txt .

# Run the app
CMD ["python", "app.py"]
