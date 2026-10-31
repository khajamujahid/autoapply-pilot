import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "uploads").mkdir(exist_ok=True)
(DATA_DIR / "artifacts").mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'autoapply.db'}")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# DRY_RUN=true (default): fill forms + screenshot, never click submit.
DRY_RUN = os.getenv("DRY_RUN", "true").lower() not in ("0", "false", "no")
REQUIRE_REVIEW = os.getenv("REQUIRE_REVIEW", "true").lower() not in ("0", "false", "no")
MIN_MATCH_SCORE = float(os.getenv("MIN_MATCH_SCORE", "0"))
HEADLESS = os.getenv("HEADLESS", "true").lower() not in ("0", "false", "no")
DAILY_QUOTA = int(os.getenv("DAILY_QUOTA", "25"))
