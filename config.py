import os
from dotenv import load_dotenv

load_dotenv()

# Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

# Admin IDs (comma-separated)
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip().isdigit()]

# Force Join settings
FORCE_CHANNEL_ID = os.getenv("FORCE_CHANNEL_ID")
FORCE_GROUP_ID = os.getenv("FORCE_GROUP_ID")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/")
GROUP_LINK = os.getenv("GROUP_LINK", "https://t.me/")

# Convert to int if provided
if FORCE_CHANNEL_ID:
    try:
        FORCE_CHANNEL_ID = int(FORCE_CHANNEL_ID)
    except ValueError:
        FORCE_CHANNEL_ID = None

if FORCE_GROUP_ID:
    try:
        FORCE_GROUP_ID = int(FORCE_GROUP_ID)
    except ValueError:
        FORCE_GROUP_ID = None

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Translation & Broadcast settings
MAX_MESSAGE_LENGTH = 4096
TRANSLATION_CHUNK_SIZE = 3000
BROADCAST_CONCURRENCY = 15
BROADCAST_DELAY = 0.05  # seconds between sends to avoid flood
