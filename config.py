import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))

GROQ_COMPLETION_MODEL = "groq/compound"
GROQ_EMBED_MODEL = "nomic-embed-text"

DB_PATH = os.getenv("DB_PATH", "data/brain.db")

RATE_LIMITS = {"/convert": 300, "/log": 300}

VEC0_BUNDLED_PATH = os.getenv(
    "VEC0_PATH",
    os.path.join(os.path.dirname(__file__), "lib", "vec0-linux-x64.so"),
)
