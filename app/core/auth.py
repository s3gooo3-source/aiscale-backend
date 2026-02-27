from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.clients import db

bearer = HTTPBearer(auto_error=False)

async def get_user(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    if not creds:
        raise HTTPException(401, "Not authenticated")
    token = creds.credentials
    try:
        # Verify token with Supabase
        res = db.auth.get_user(token)
        user = res.user
        if not user:
            raise HTTPException(401, "Invalid token")
        return {"user_id": str(user.id), "email": user.email}
    except Exception as e:
        raise HTTPException(401, f"Auth error: {e}")
