FROM python:3.10-slim

# Install system dependencies including ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Force Python to output logs immediately to Render's dashboard
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot code
COPY . .

CMD ["python", "bot.py"]
