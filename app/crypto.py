from cryptography.fernet import Fernet, InvalidToken
from .config import ENCRYPTION_KEY

_f = Fernet(ENCRYPTION_KEY.encode())


def enc(s: str) -> str:
    return _f.encrypt(s.encode()).decode() if s else ""


def dec(s: str) -> str:
    if not s:
        return ""
    try:
        return _f.decrypt(s.encode()).decode()
    except InvalidToken:
        return ""
