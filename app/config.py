import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))


_load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()
for d in ("", "uploads", "artifacts"):
    (DATA_DIR / d).mkdir(parents=True, exist_ok=True)


def _persistent_secret(name: str, gen) -> str:
    """Use env var if set; otherwise generate once and keep in ./data so restarts keep sessions/keys."""
    if os.getenv(name):
        return os.environ[name]
    f = DATA_DIR / f".{name.lower()}"
    if not f.exists():
        f.write_text(gen()); f.chmod(0o600)
    return f.read_text().strip()


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _rand():
    import secrets
    return secrets.token_urlsafe(48)


DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'app.db'}"
SECRET_KEY = _persistent_secret("SECRET_KEY", _rand)
ENCRYPTION_KEY = _persistent_secret("ENCRYPTION_KEY", _fernet)
BASE_URL = os.getenv("BASE_URL", "http://localhost:8077").rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "true").lower() in ("1", "true", "yes")
HEADLESS = os.getenv("HEADLESS", "true").lower() not in ("0", "false", "no")
RUN_WORKER_IN_PROCESS = os.getenv("RUN_WORKER_IN_PROCESS", "true").lower() in ("1", "true", "yes")
APP_NAME = "ApplyPilot"
