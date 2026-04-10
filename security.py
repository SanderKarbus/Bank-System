from fastapi.security import HTTPBearer
from fastapi import Depends, HTTPException

security = HTTPBearer(auto_error=False)

def verify_user(authorization: str = Depends(security)):
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Bearer token required"},
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    from auth import verify_token
    payload = verify_token(authorization.credentials)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or expired token"},
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    return payload
