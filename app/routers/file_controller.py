"""
Router FastAPI para Gestión Segura de Archivos (/files/*).
Cada endpoint exige Bearer Token, verifica su permiso individual en permissions.json,
valida que la ruta esté dentro de allowed_paths (fuera de carpetas del sistema),
verifica que la operación se haya guardado realmente en disco y registra toda acción en audit.log.
"""

from fastapi import APIRouter, Depends, Request
from app.core.security import verify_bearer_token
from app.core.logger import log_audit_event
from app.core.permissions import load_permissions, check_kill_switch
from app.schemas.files import (
    FileListRequest,
    FileListResponse,
    FileReadRequest,
    FileReadResponse,
    FileWriteRequest,
    FileWriteResponse,
    FilePatchEditRequest,
    FilePatchEditResponse,
    FileMkdirRequest,
    FileDeleteRequest,
    FileMoveRequest,
    FileSearchRequest,
    FileUploadRequest,
    FileDownloadRequest,
    FileDownloadResponse,
)
from app.services import file_service

router = APIRouter(
    prefix="/files",
    tags=["File Management (Sandboxed)"],
    dependencies=[Depends(verify_bearer_token)]
)


@router.get("/allowed-paths", summary="Consultar carpetas autorizadas y permisos de archivos activos")
async def get_allowed_paths(request: Request):
    check_kill_switch()
    perms = load_permissions()
    client_ip = request.client.host if request.client else "unknown"
    result = {
        "confirmed": True,
        "allowed_paths": perms.get("allowed_paths", []),
        "permissions": {
            "allow_file_list": perms.get("allow_file_list", False),
            "allow_file_read": perms.get("allow_file_read", False),
            "allow_file_write": perms.get("allow_file_write", False),
            "allow_file_mkdir": perms.get("allow_file_mkdir", False),
            "allow_delete": perms.get("allow_delete", False),
            "allow_file_move": perms.get("allow_file_move", False),
            "allow_file_search": perms.get("allow_file_search", False),
            "allow_file_transfer": perms.get("allow_file_transfer", False),
        }
    }
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/allowed-paths",
        action="QUERY_ALLOWED_PATHS",
        details={"count": len(result["allowed_paths"])}
    )
    return result


@router.post("/list", response_model=FileListResponse, summary="Listar archivos y carpetas en una ruta autorizada")
async def list_files(payload: FileListRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.list_directory(payload.path, payload.include_hidden)
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/list",
        action="FILE_LIST",
        details={"path": result["path"], "total_items": result["total_items"]}
    )
    return result


@router.post("/read", response_model=FileReadResponse, summary="Leer contenido de un archivo dentro de una ruta autorizada")
async def read_file(payload: FileReadRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.read_file_content(payload.path, payload.encoding, payload.max_bytes)
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/read",
        action="FILE_READ",
        details={"path": result["path"], "size_bytes": result["size_bytes"], "sha256": result["sha256"]}
    )
    return result


@router.post("/write", response_model=FileWriteResponse, summary="Crear o sobrescribir archivo con respaldo automático .bak y verificación en disco")
async def write_file(payload: FileWriteRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.write_file_content(payload.path, payload.content, payload.append, payload.encoding)
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/write",
        action="FILE_WRITE",
        details={
            "path": result["path"],
            "bytes_written": result["bytes_written"],
            "sha256": result["sha256"],
            "backup_created": result["backup_created"],
            "backup_path": result["backup_path"],
            "confirmed": True
        }
    )
    return result


@router.patch("/edit", response_model=FilePatchEditResponse, summary="Editar archivo existente por búsqueda/reemplazo con respaldo .bak y verificación estricta")
@router.post("/edit", response_model=FilePatchEditResponse, summary="Editar archivo existente por búsqueda/reemplazo con respaldo .bak y verificación estricta")
async def edit_file_patch(payload: FilePatchEditRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.patch_edit_file_content(
        raw_path=payload.path,
        target_text=payload.target_text,
        replacement_text=payload.replacement_text,
        replace_all=payload.replace_all,
        encoding=payload.encoding
    )
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/edit",
        action="FILE_PATCH_EDIT",
        details={
            "path": result["path"],
            "replacements_made": result["replacements_made"],
            "sha256_before": result["sha256_before"],
            "sha256_after": result["sha256_after"],
            "backup_path": result["backup_path"],
            "confirmed": True
        }
    )
    return result


@router.post("/mkdir", summary="Crear carpeta dentro de una ruta autorizada")
async def make_directory(payload: FileMkdirRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.create_directory(payload.path)
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/mkdir",
        action="FILE_MKDIR",
        details={"path": result["path"], "confirmed": True}
    )
    return result


@router.post("/delete", summary="Eliminar archivo o carpeta enviándolo exclusivamente a la Papelera de Reciclaje")
async def delete_file(payload: FileDeleteRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.delete_to_recycle_bin(payload.path)
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/delete",
        action="FILE_DELETE_TO_RECYCLE_BIN",
        details={"path": result["path"], "permanent_delete": False, "confirmed": True}
    )
    return result


@router.post("/move", summary="Mover o renombrar archivo o carpeta dentro de rutas autorizadas")
async def move_file(payload: FileMoveRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.move_or_rename(payload.source_path, payload.destination_path, payload.overwrite)
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/move",
        action="FILE_MOVE",
        details={
            "source": result["source_path"],
            "destination": result["destination_path"],
            "backup_created": result["backup_created"],
            "confirmed": True
        }
    )
    return result


@router.post("/search", summary="Buscar archivos por nombre y/o contenido dentro de una ruta autorizada")
async def search_files(payload: FileSearchRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.search_files(
        base_raw=payload.base_path,
        query=payload.query,
        search_by_name=payload.search_by_name,
        search_by_content=payload.search_by_content,
        case_sensitive=payload.case_sensitive,
        max_results=payload.max_results
    )
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/search",
        action="FILE_SEARCH",
        details={
            "base_path": result["base_path"],
            "query": payload.query,
            "search_by_name": payload.search_by_name,
            "search_by_content": payload.search_by_content,
            "total_matches": result["total_matches"]
        }
    )
    return result


@router.post("/upload", summary="Subir archivo codificado en Base64 a una ruta autorizada (con respaldo .bak si existe)")
async def upload_file(payload: FileUploadRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.upload_file_base64(payload.destination_path, payload.base64_data)
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/upload",
        action="FILE_UPLOAD",
        details={
            "path": result["path"],
            "size_bytes": result["size_bytes"],
            "sha256": result["sha256"],
            "backup_created": result["backup_created"],
            "confirmed": True
        }
    )
    return result


@router.post("/download", response_model=FileDownloadResponse, summary="Descargar archivo en Base64 desde una ruta autorizada")
async def download_file(payload: FileDownloadRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    result = file_service.download_file_base64(payload.path)
    log_audit_event(
        client_ip=client_ip,
        endpoint="/files/download",
        action="FILE_DOWNLOAD",
        details={"path": result["path"], "size_bytes": result["size_bytes"], "sha256": result["sha256"]}
    )
    return result
