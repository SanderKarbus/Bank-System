from datetime import datetime, timedelta
from typing import Optional
import jwt
import secrets

SECRET_KEY = secrets.token_hex(32)
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

_tokens = {}


def create_user_token(user_id: str, full_name: str) -> dict:
    exp = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "name": full_name,
        "exp": exp,
        "type": "user_access"
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    _tokens[user_id] = {
        "token": token,
        "expires_at": exp,
        "full_name": full_name
    }
    
    return {
        "accessToken": token,
        "tokenType": "Bearer",
        "expiresAt": exp
    }


def verify_bearer_token(authorization: Optional[str]) -> Optional[dict]:
    if not authorization:
        return None
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    
    token = parts[1]
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        if payload.get("type") != "user_access":
            return None
        
        user_id = payload.get("sub")
        if user_id and user_id in _tokens:
            stored = _tokens[user_id]
            if stored["expires_at"] > datetime.utcnow():
                return {"user_id": user_id, "full_name": payload.get("name")}
        
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def invalidate_token(user_id: str):
    if user_id in _tokens:
        del _tokens[user_id]
