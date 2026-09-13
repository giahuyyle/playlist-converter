import base64
import hashlib
import time

import jwt
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request

from app.core.config import config
from app.core.database import get_db
from app.models.entities import User


def cipher():
    key = config.token_encryption_key or base64.urlsafe_b64encode(
        hashlib.sha256(config.secret_key.encode()).digest()
    )
    return Fernet(key)


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    return cipher().decrypt(value.encode()).decode()


def session_token(user_id):
    return jwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 86400 * 30, "purpose": "session"},
        config.secret_key,
        algorithm="HS256",
    )


def current_user(request: Request, db=Depends(get_db)):
    try:
        payload = jwt.decode(
            request.cookies.get("session", ""), config.secret_key, algorithms=["HS256"]
        )
        if payload.get("purpose") != "session":
            raise ValueError()
        user = db.get(User, payload["sub"])
        if not user:
            raise ValueError()
        return user
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Start a session first") from None
