from fastapi import APIRouter, Depends, Request, HTTPException, status
from app.core.security import verify_bearer_token
from app.core.logger import log_audit_event
from app.core.permissions import check_permission
from app.schemas.whatsapp import WhatsAppSendRequest
from app.services.whatsapp_service import get_whatsapp_adapter

router = APIRouter(
    prefix="/whatsapp",
    tags=["WhatsApp"],
    dependencies=[Depends(verify_bearer_token)]
)


@router.post("/send")
async def send_whatsapp_message(body: WhatsAppSendRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    check_permission(
        "allow_whatsapp",
        "El envío de mensajes por WhatsApp está desactivado por el usuario en el panel de control."
    )
    adapter = get_whatsapp_adapter()
    try:
        result = await adapter.send_message(body.phone_number, body.message)
        log_audit_event(
            client_ip=client_ip,
            endpoint="/whatsapp/send",
            action="WHATSAPP_MESSAGE_SENT",
            details={
                "phone_number": body.phone_number,
                "message_length": len(body.message),
                "provider": result.get("provider")
            }
        )
        return result
    except Exception as exc:
        log_audit_event(
            client_ip=client_ip,
            endpoint="/whatsapp/send",
            action="WHATSAPP_SEND_FAILED",
            details={"phone_number": body.phone_number, "error": str(exc)},
            status="ERROR"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Fallo al enviar mensaje WhatsApp: {str(exc)}"
        )
