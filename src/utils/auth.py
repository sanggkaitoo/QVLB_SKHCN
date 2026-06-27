import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from src.core import config

security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    """Kiểm tra tài khoản/mật khẩu Admin."""
    is_user_ok = secrets.compare_digest(credentials.username, config.ADMIN_USER)
    is_pass_ok = secrets.compare_digest(credentials.password, config.ADMIN_PASS)
    
    if not (is_user_ok and is_pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản hoặc mật khẩu không chính xác",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username