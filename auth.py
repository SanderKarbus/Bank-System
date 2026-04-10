from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, ExpiredSignatureError, JWTError

SECRET_KEY = "this-is-a-fixed-secret-key-for-testing-only-do-not-use-in-production"
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


def verify_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        if payload.get("type") != "user_access":
            return None
        
        return {"user_id": payload.get("sub"), "full_name": payload.get("name")}
    except Exception as e:
        print(f"Token verification failed: {e}")
        return None


def invalidate_token(user_id: str):
    pass
