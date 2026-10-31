import re
from fastapi import Request, HTTPException
from passlib.context import CryptContext
from .db import SessionLocal, User, ensure_user_rows
from .config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth = None
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    from authlib.integrations.starlette_client import OAuth
    oauth = OAuth()
    oauth.register("google", client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET,
                   server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid email profile"})


class LoginRequired(Exception):
    pass


def current_user(request: Request) -> User:
    uid = request.session.get("uid")
    if uid:
        with SessionLocal() as s:
            u = s.get(User, uid)
            if u:
                return u
    raise LoginRequired()


def valid_email(e):
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e or "") is not None


def create_user(email, password="", name="", google_sub=""):
    with SessionLocal() as s:
        u = User(email=email.lower().strip(), name=name, google_sub=google_sub,
                 password_hash=pwd.hash(password) if password else "")
        s.add(u); s.commit()
        ensure_user_rows(s, u.id)
        from .db import Profile
        p = s.get(Profile, u.id); p.data = {**(p.data or {}), "email": u.email,
                                            "first_name": (name.split(" ")[0] if name else ""),
                                            "last_name": (" ".join(name.split(" ")[1:]) if name else "")}
        s.commit()
        return u
