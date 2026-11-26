import secrets
from fastapi import Security, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import settings
from app.core.logger import log_audit_event

bearer_scheme = HTTPBearer(auto_error=True)


async def verify_bearer_token(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)
) -> str:
    token = credentials.credentials
    is_valid = secrets.compare_digest(token, settings.API_SECRET_TOKEN)

    if not is_valid:
        log_audit_event(
            client_ip="unknown",
            endpoint="auth",
            action="TOKEN_VERIFICATION_FAILED",
            details={"provided_token_length": len(token)},
            status="UNAUTHORIZED"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autorización inválido o no autorizado.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token
