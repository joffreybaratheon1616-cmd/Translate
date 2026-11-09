# config.py — environment-only configuration
import os
from typing import List, Optional

BOT_TOKEN: str = os.environ["BOT_TOKEN"]

ADMIN_IDS: List[int] = [
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
]

FORCE_CHANNEL_ID: Optional[int] = (
    int(os.environ["FORCE_CHANNEL_ID"]) if os.environ.get("FORCE_CHANNEL_ID") else None
)
FORCE_GROUP_ID: Optional[int] = (
    int(os.environ["FORCE_GROUP_ID"]) if os.environ.get("FORCE_GROUP_ID") else None
)
CHANNEL_LINK: str = os.environ.get("CHANNEL_LINK", "")
GROUP_LINK: str = os.environ.get("GROUP_LINK", "")

DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///bot.db")

# Translation: deep-translator GoogleTranslator
TRANSLATE_TARGET: str = "en"
TRANSLATE_MAX_CHUNK: int = 4000  # safe under Google's ~5000 char practical cap

# Broadcast throttling
BROADCAST_CONCURRENCY: int = 10
BROADCAST_DELAY: float = 0.05  # seconds between sends per worker

LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
