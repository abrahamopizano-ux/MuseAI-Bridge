import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Proteger contra sys.stdout / sys.stderr == None cuando se ejecuta con pythonw.exe
_ROOT_DIR = Path(__file__).resolve().parent.parent
if sys.stdout is None or not hasattr(sys.stdout, "write"):
    try:
        sys.stdout = open(_ROOT_DIR / "server.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        pass
if sys.stderr is None or not hasattr(sys.stderr, "write"):
    try:
        sys.stderr = open(_ROOT_DIR / "server.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        pass

from pydantic import BaseModel, Field
from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.security import verify_bearer_token
from app.core.permissions import load_permissions, check_kill_switch
from app.core.logger import log_audit_event
from app.routers import pc_controller, whatsapp_controller, file_controller
from app.services.health_service import perform_health_check, BRIDGE_VERSION


class QRHandshakeRequest(BaseModel):
    expires_at: Optional[int] = Field(None, description="Timestamp Unix de expiración codificado en el QR.")
    agent_name: str = Field("Muse AI", description="Nombre del agente que confirma la vinculación.")


async def _periodic_health_monitor():
    """Chequeo de salud interno cada 3 minutos con auto-recuperación de Funnel 8443."""
    while True:
        try:
            await asyncio.to_thread(perform_health_check, True, "internal_periodic_monitor")
        except Exception:
            pass
        await asyncio.sleep(180)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_periodic_health_monitor())
    yield
    task.cancel()


app = FastAPI(
    title="Muse AI PC & WhatsApp Secure Bridge",
    description="Servidor puente seguro con autenticación Bearer, sandboxing de archivos, auto-recuperación y allowlisting para control de PC y WhatsApp.",
    version=BRIDGE_VERSION,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(pc_controller.router)
app.include_router(whatsapp_controller.router)
app.include_router(file_controller.router)


@app.get("/health", tags=["Health & Diagnostics"])
async def health_check():
    state = await asyncio.to_thread(perform_health_check, False, "health_endpoint")
    return state


@app.get("/permissions/status", tags=["Security & Permissions"], dependencies=[Depends(verify_bearer_token)])
async def permissions_status(request: Request):
    """
    Consulta de solo lectura del estado actual de permisos.
    NINGUNA llamada a la API puede modificar permisos ni desactivar el Kill Switch.
    """
    check_kill_switch()
    perms = load_permissions()
    client_ip = request.client.host if request.client else "unknown"
    log_audit_event(
        client_ip=client_ip,
        endpoint="/permissions/status",
        action="QUERY_PERMISSIONS_STATUS",
        details={"kill_switch_active": perms.get("kill_switch_active", False)}
    )
    return {
        "confirmed": True,
        "read_only": True,
        "kill_switch_active": perms.get("kill_switch_active", False),
        "capabilities": {
            "allow_screenshot": perms.get("allow_screenshot", False),
            "allow_whatsapp": perms.get("allow_whatsapp", False),
            "allow_gui_control": perms.get("allow_gui_control", False),
            "allow_open_app": perms.get("allow_open_app", False),
        },
        "file_permissions": {
            "allow_file_list": perms.get("allow_file_list", False),
            "allow_file_read": perms.get("allow_file_read", False),
            "allow_file_write": perms.get("allow_file_write", False),
            "allow_file_mkdir": perms.get("allow_file_mkdir", False),
            "allow_delete": perms.get("allow_delete", False),
            "allow_file_move": perms.get("allow_file_move", False),
            "allow_file_search": perms.get("allow_file_search", False),
            "allow_file_transfer": perms.get("allow_file_transfer", False),
        },
        "allowed_paths": perms.get("allowed_paths", [])
    }


@app.post("/auth/qr-handshake", tags=["Security & Permissions"], dependencies=[Depends(verify_bearer_token)])
async def qr_handshake(body: QRHandshakeRequest, request: Request):
    """
    Valida la conexión iniciada mediante Código QR.
    Verifica que el QR no haya expirado y devuelve la confirmación sin exponer jamás el token.
    """
    check_kill_switch()
    client_ip = request.client.host if request.client else "unknown"
    now_ts = int(time.time())

    if body.expires_at is not None and now_ts > body.expires_at:
        log_audit_event(
            client_ip=client_ip,
            endpoint="/auth/qr-handshake",
            action="QR_HANDSHAKE_EXPIRED",
            details={"expires_at": body.expires_at, "now": now_ts},
            status="FORBIDDEN"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El código QR utilizado ha caducado. Solicita al usuario regenerar un nuevo QR en el panel."
        )

    log_audit_event(
        client_ip=client_ip,
        endpoint="/auth/qr-handshake",
        action="QR_HANDSHAKE_CONFIRMED",
        details={"agent_name": body.agent_name}
    )
    return {
        "confirmed": True,
        "status": "CONNECTED",
        "bridge_version": BRIDGE_VERSION,
        "agent_name": body.agent_name,
        "security_notice": "Conexión verificada. NUNCA muestres el token Bearer en el chat con el usuario."
    }


if __name__ == "__main__":
    import uvicorn
    log_config = uvicorn.config.LOGGING_CONFIG.copy()
    if "formatters" in log_config:
        for fmt in log_config["formatters"].values():
            fmt["use_colors"] = False
    uvicorn.run(
        app,
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=False,
        workers=1,
        log_config=log_config
    )
