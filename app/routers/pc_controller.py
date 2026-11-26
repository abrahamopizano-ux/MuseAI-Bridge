from typing import Optional
from fastapi import APIRouter, Depends, Query, Request, HTTPException, status
from app.core.security import verify_bearer_token
from app.core.logger import log_audit_event
from app.core.permissions import check_permission, check_app_permission, check_kill_switch, load_permissions
from app.schemas.pc import (
    OpenAppRequest,
    TypeAndClickBatchRequest,
    TypeLongTextRequest,
    ScreenshotResponse,
    SystemInfoResponse
)
from app.services.pc_service import PCService

router = APIRouter(
    prefix="/pc",
    tags=["PC Control"],
    dependencies=[Depends(verify_bearer_token)]
)


@router.get("/allowed-apps", summary="Listar aplicaciones instaladas que el usuario ha autorizado abrir")
async def get_allowed_apps(request: Request):
    check_kill_switch()
    client_ip = request.client.host if request.client else "unknown"
    perms = load_permissions()
    allowed_map = perms.get("allowed_apps", {})
    catalog = perms.get("installed_apps_catalog", {})
    authorized_list = [
        {
            "id": app_id,
            "name": catalog.get(app_id, {}).get("name", app_id),
            "source": catalog.get(app_id, {}).get("source", "Sistema")
        }
        for app_id, is_ok in allowed_map.items()
        if is_ok
    ]
    log_audit_event(
        client_ip=client_ip,
        endpoint="/pc/allowed-apps",
        action="LIST_ALLOWED_APPS",
        details={"authorized_count": len(authorized_list)}
    )
    return {
        "confirmed": True,
        "allow_open_app": perms.get("allow_open_app", False),
        "authorized_apps": authorized_list
    }


@router.post("/open-app", summary="Abrir una aplicación autorizada en la lista blanca del usuario")
async def open_app(body: OpenAppRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    check_app_permission(body.app_name)
    try:
        result = PCService.open_allowed_application(body.app_name)
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/open-app",
            action="OPEN_APP",
            details={"app_name": body.app_name, "pid": result.get("pid"), "confirmed": result.get("confirmed")}
        )
        return result
    except ValueError as exc:
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/open-app",
            action="OPEN_APP_REJECTED",
            details={"app_name": body.app_name, "error": str(exc)},
            status="FORBIDDEN"
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except Exception as exc:
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/open-app",
            action="OPEN_APP_ERROR",
            details={"app_name": body.app_name, "error": str(exc)},
            status="ERROR"
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/type-and-click", summary="Ejecutar secuencia de acciones de mouse y teclado (con soporte Unicode y conversión de coordenadas)")
async def type_and_click(body: TypeAndClickBatchRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    check_permission(
        "allow_gui_control",
        "El control de mouse y teclado está desactivado por el usuario en el panel de control."
    )
    try:
        result = PCService.execute_gui_actions(
            body.actions,
            source_width=body.source_width,
            source_height=body.source_height
        )
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/type-and-click",
            action="GUI_ACTIONS_BATCH",
            details={
                "actions_count": len(body.actions),
                "confirmed": result.get("confirmed"),
                "final_cursor": result.get("final_cursor_position")
            }
        )
        return result
    except Exception as exc:
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/type-and-click",
            action="GUI_ACTIONS_ERROR",
            details={"error": str(exc)},
            status="ERROR"
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/type-text", summary="Enviar texto largo de una sola vez con soporte nativo para acentos (áéíóúñ) y saltos de línea")
async def type_long_text(body: TypeLongTextRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    check_permission(
        "allow_gui_control",
        "El control de mouse y teclado está desactivado por el usuario en el panel de control."
    )
    try:
        result = PCService.type_long_text(
            text=body.text,
            x=body.x,
            y=body.y,
            press_enter=body.press_enter,
            use_clipboard=body.use_clipboard
        )
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/type-text",
            action="TYPE_LONG_TEXT",
            details={
                "characters_sent": result["characters_sent"],
                "lines_sent": result["lines_sent"],
                "method": result["method"],
                "press_enter": body.press_enter,
                "confirmed": True
            }
        )
        return result
    except Exception as exc:
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/type-text",
            action="TYPE_LONG_TEXT_ERROR",
            details={"error": str(exc)},
            status="ERROR"
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/screenshot", response_model=ScreenshotResponse, summary="Captura de pantalla en resolución real (1920x1080) en Base64 JPEG")
async def screenshot(
    request: Request,
    native: bool = Query(True, description="Si es True (por defecto), devuelve la captura en la resolución nativa real del monitor (1920x1080) para coordenadas 1:1."),
    max_width: Optional[int] = Query(None, ge=640, le=7680, description="Ancho máximo opcional si native=False."),
    quality: Optional[int] = Query(85, ge=30, le=100, description="Calidad JPEG (30-100)."),
    x: Optional[int] = Query(None, ge=0, description="Coordenada X inicial para captura de región."),
    y: Optional[int] = Query(None, ge=0, description="Coordenada Y inicial para captura de región."),
    w: Optional[int] = Query(None, ge=16, le=7680, description="Ancho de la región a recortar."),
    h: Optional[int] = Query(None, ge=16, le=4320, description="Alto de la región a recortar."),
    zoom: float = Query(1.0, ge=1.0, le=4.0, description="Factor de ampliación (zoom 1.0 a 4.0) para inspeccionar detalles finos.")
):
    client_ip = request.client.host if request.client else "unknown"
    check_permission(
        "allow_screenshot",
        "La captura de pantalla está desactivada por el usuario en el panel de control."
    )
    try:
        data = PCService.capture_optimized_screenshot(
            native_resolution=native,
            max_width=max_width,
            quality=quality,
            region_x=x,
            region_y=y,
            region_w=w,
            region_h=h,
            zoom=zoom
        )
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/screenshot",
            action="SCREENSHOT_CAPTURED",
            details={
                "width": data["width"],
                "height": data["height"],
                "native_width": data["native_width"],
                "native_height": data["native_height"],
                "region": [x, y, w, h] if x is not None else None,
                "zoom": data["zoom_applied"]
            }
        )
        return data
    except Exception as exc:
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/screenshot",
            action="SCREENSHOT_ERROR",
            details={"error": str(exc)},
            status="ERROR"
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/screenshot-hd", response_model=ScreenshotResponse, summary="Captura Full HD nativa 1920x1080 garantizada (coordenadas 1:1 sin reescalado)")
async def screenshot_hd(
    request: Request,
    quality: int = Query(92, ge=50, le=100, description="Calidad JPEG de alta definición (por defecto 92).")
):
    client_ip = request.client.host if request.client else "unknown"
    check_permission(
        "allow_screenshot",
        "La captura de pantalla está desactivada por el usuario en el panel de control."
    )
    try:
        data = PCService.capture_optimized_screenshot(native_resolution=True, quality=quality)
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/screenshot-hd",
            action="SCREENSHOT_HD_CAPTURED",
            details={"width": data["width"], "height": data["height"], "quality": quality}
        )
        return data
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/screenshot-region", response_model=ScreenshotResponse, summary="Captura de una región específica de la pantalla con zoom opcional (lupa de precisión)")
async def screenshot_region(
    request: Request,
    x: int = Query(..., ge=0, description="Coordenada X superior izquierda en resolución real (1920x1080)."),
    y: int = Query(..., ge=0, description="Coordenada Y superior izquierda en resolución real (1920x1080)."),
    width: int = Query(480, ge=32, le=1920, description="Ancho del recorte en píxeles reales."),
    height: int = Query(320, ge=32, le=1080, description="Alto del recorte en píxeles reales."),
    zoom: float = Query(2.0, ge=1.0, le=4.0, description="Factor de zoom Lanczos (ej. 2.0 duplica el tamaño para leer texto pequeño).")
):
    client_ip = request.client.host if request.client else "unknown"
    check_permission(
        "allow_screenshot",
        "La captura de pantalla está desactivada por el usuario en el panel de control."
    )
    try:
        data = PCService.capture_optimized_screenshot(
            native_resolution=True,
            quality=95,
            region_x=x,
            region_y=y,
            region_w=width,
            region_h=height,
            zoom=zoom
        )
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/screenshot-region",
            action="SCREENSHOT_REGION_ZOOM",
            details={"x": x, "y": y, "width": width, "height": height, "zoom": zoom}
        )
        return data
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/system-info", response_model=SystemInfoResponse)
async def system_info(request: Request):
    check_kill_switch()
    client_ip = request.client.host if request.client else "unknown"
    try:
        metrics = PCService.get_system_metrics()
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/system-info",
            action="SYSTEM_INFO_READ",
            details={"cpu": metrics["cpu_percent"], "ram": metrics["memory_percent"]}
        )
        return metrics
    except Exception as exc:
        log_audit_event(
            client_ip=client_ip,
            endpoint="/pc/system-info",
            action="SYSTEM_INFO_ERROR",
            details={"error": str(exc)},
            status="ERROR"
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
