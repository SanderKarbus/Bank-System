from datetime import datetime, timedelta
from typing import Optional
import secrets
import os
from jose import jwt, ExpiredSignatureError

SECRET_KEY = os.getenv("JWT_SECRET", secrets.token_hex(32))
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 8760


def create_user_token(user_id: str, full_name: str) -> dict:
    exp = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "name": full_name,
        "exp": exp,
        "type": "user_access"
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
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
        
        return {"user_id": payload.get("sub"), "full_name": payload.get("name")}
    except ExpiredSignatureError:
        return None
    except Exception:
        return None
