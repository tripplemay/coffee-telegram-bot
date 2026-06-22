import os
import tempfile

from cryptography.fernet import Fernet

# Isolate tests: temp DB + a throwaway Fernet key + dummy creds (override .env).
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("AIGC_API_KEY", "pk_test")
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("DAILY_SPEND_LIMIT", "100")

from core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from core import db  # noqa: E402

db.init_db()
