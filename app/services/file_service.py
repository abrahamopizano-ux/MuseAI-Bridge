"""
Servicio de Gestión Segura de Archivos con Sandboxing, Respaldo Automático (.bak),
Búsqueda por Nombre y Contenido, Verificación Criptográfica (SHA-256)
y Eliminación Exclusiva a Papelera de Reciclaje (Win32 SHFileOperationW).
"""

import os
import base64
import hashlib
import shutil
import ctypes
import fnmatch
import mimetypes
from ctypes import wintypes
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List
from fastapi import HTTPException, status

from app.core.permissions import validate_authorized_path, is_forbidden_system_path

# Extensiones de texto seguro para búsqueda por contenido
TEXT_SEARCH_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".log", ".py", ".js", ".ts", ".html",
    ".css", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".bat", ".ps1", ".sql",
    ".env", ".toml", ".rst", ".tex"
}


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", wintypes.LPVOID),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


def _compute_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _send_to_windows_recycle_bin(target: Path) -> None:
    """
    Envía un archivo o carpeta directamente a la Papelera de Reciclaje de Windows
    usando shell32.SHFileOperationW con FOF_ALLOWUNDO.
    Verifica posteriormente que el archivo ya no exista en la ruta original.
    """
    abs_path = str(target.resolve()) + "\0\0"
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = abs_path
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None

    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if res != 0 or op.fAnyOperationsAborted or target.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"No se pudo confirmar el envío de '{target.name}' a la Papelera de Reciclaje (Código Win32: {res})."
        )


def _create_bak_backup(target: Path) -> str:
    """
    Crea una copia de respaldo .bak del archivo existente antes de modificarlo o sobrescribirlo
    y verifica en disco que el respaldo exista.
    """
    bak_path = target.with_name(target.name + ".bak")
    shutil.copy2(target, bak_path)
    if not bak_path.exists():
        raise RuntimeError(f"Fallo crítico: No se pudo verificar la creación del respaldo '{bak_path}'.")
    return str(bak_path)


def _format_item(p: Path) -> Dict[str, Any]:
    try:
        st = p.stat()
        size = st.st_size if p.is_file() else 0
        mtime = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    except Exception:
        size = 0
        mtime = ""
    return {
        "name": p.name,
        "path": str(p),
        "is_dir": p.is_dir(),
        "size_bytes": size,
        "modified_at": mtime,
        "extension": p.suffix.lower() if p.is_file() else ""
    }


def list_directory(raw_path: str, include_hidden: bool = False) -> Dict[str, Any]:
    target = validate_authorized_path(
        raw_path,
        "allow_file_list",
        "El permiso para listar archivos y carpetas (allow_file_list) está desactivado.",
        endpoint="/files/list"
    )
    if not target.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"La ruta '{target}' no existe.")
    if not target.is_dir():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"La ruta '{target}' no es una carpeta.")

    items: List[Dict[str, Any]] = []
    try:
        for child in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if not include_hidden and child.name.startswith("."):
                continue
            if is_forbidden_system_path(child):
                continue
            items.append(_format_item(child))
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sin permisos del sistema operativo para leer esta carpeta.")

    return {
        "confirmed": True,
        "path": str(target),
        "total_items": len(items),
        "items": items
    }


def read_file_content(raw_path: str, encoding: str = "utf-8", max_bytes: int = 524288) -> Dict[str, Any]:
    target = validate_authorized_path(
        raw_path,
        "allow_file_read",
        "El permiso de lectura de archivos (allow_file_read) está desactivado.",
        endpoint="/files/read"
    )
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"El archivo '{target}' no existe.")

    size = target.stat().st_size
    truncated = size > max_bytes

    try:
        with open(target, "rb") as f:
            raw_bytes = f.read(max_bytes)
        content = raw_bytes.decode(encoding, errors="replace")
        file_hash = _compute_sha256(target)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error al leer el archivo: {exc}")

    return {
        "confirmed": True,
        "path": str(target),
        "size_bytes": size,
        "sha256": file_hash,
        "truncated": truncated,
        "content": content
    }


def write_file_content(raw_path: str, content: str, append: bool = False, encoding: str = "utf-8") -> Dict[str, Any]:
    target = validate_authorized_path(
        raw_path,
        "allow_file_write",
        "El permiso para crear o editar archivos (allow_file_write) está desactivado.",
        endpoint="/files/write"
    )
    if target.exists() and target.is_dir():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La ruta destino es una carpeta, no un archivo.")

    validate_authorized_path(
        str(target.parent),
        "allow_file_write",
        "La carpeta contenedora no está autorizada.",
        endpoint="/files/write"
    )

    backup_created = False
    backup_path = None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_file():
            backup_path = _create_bak_backup(target)
            backup_created = True

        encoded_bytes = content.encode(encoding)
        if append and target.exists():
            with open(target, "ab") as f:
                f.write(encoded_bytes)
                f.flush()
                os.fsync(f.fileno())
        else:
            temp_file = target.with_suffix(target.suffix + ".tmp_write")
            with open(temp_file, "wb") as f:
                f.write(encoded_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, target)

        if not target.exists() or not target.is_file():
            raise RuntimeError("El archivo no se encontró en disco después de la escritura.")

        disk_size = target.stat().st_size
        new_sha256 = _compute_sha256(target)
        if not append and disk_size != len(encoded_bytes):
            raise RuntimeError(f"Verificación fallida: tamaño en disco ({disk_size}) no coincide con bytes escritos ({len(encoded_bytes)}).")

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error al escribir y verificar el archivo: {exc}")

    return {
        "confirmed": True,
        "status": "SUCCESS",
        "path": str(target),
        "bytes_written": len(encoded_bytes),
        "file_size_on_disk": disk_size,
        "sha256": new_sha256,
        "backup_created": backup_created,
        "backup_path": backup_path
    }


def patch_edit_file_content(
    raw_path: str,
    target_text: str,
    replacement_text: str,
    replace_all: bool = False,
    encoding: str = "utf-8"
) -> Dict[str, Any]:
    """
    Edita un archivo existente reemplazando target_text por replacement_text.
    - Crea un respaldo .bak obligatorio antes de modificar.
    - Si target_text no existe en el archivo o el contenido resultante no cambia en disco,
      devuelve HTTP 422 Unprocessable Entity (NUNCA devuelve ok:true si no guardó cambios reales).
    """
    target = validate_authorized_path(
        raw_path,
        "allow_file_write",
        "El permiso para crear o editar archivos (allow_file_write) está desactivado.",
        endpoint="/files/edit"
    )
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"El archivo '{target}' no existe.")

    try:
        sha256_before = _compute_sha256(target)
        with open(target, "r", encoding=encoding) as f:
            original_content = f.read()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"No se pudo decodificar el archivo con {encoding}: {exc}")

    occurrences = original_content.count(target_text)
    if occurrences == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Operación rechazada (0 cambios): El texto buscado (target_text) no existe en el archivo. No se modificó nada."
        )

    if target_text == replacement_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Operación rechazada (0 cambios): replacement_text es idéntico a target_text."
        )

    backup_path = _create_bak_backup(target)
    replacements_made = occurrences if replace_all else 1
    new_content = original_content.replace(target_text, replacement_text) if replace_all else original_content.replace(target_text, replacement_text, 1)

    encoded_bytes = new_content.encode(encoding)
    temp_file = target.with_suffix(target.suffix + ".tmp_patch")
    with open(temp_file, "wb") as f:
        f.write(encoded_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_file, target)

    sha256_after = _compute_sha256(target)
    if sha256_after == sha256_before:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Verificación en disco fallida: El hash SHA-256 del archivo no cambió tras la edición."
        )

    return {
        "confirmed": True,
        "status": "PATCH_APPLIED_AND_VERIFIED",
        "path": str(target),
        "replacements_made": replacements_made,
        "bytes_written": len(encoded_bytes),
        "sha256_before": sha256_before,
        "sha256_after": sha256_after,
        "backup_created": True,
        "backup_path": backup_path
    }


def create_directory(raw_path: str) -> Dict[str, Any]:
    target = validate_authorized_path(
        raw_path,
        "allow_file_mkdir",
        "El permiso para crear carpetas (allow_file_mkdir) está desactivado.",
        endpoint="/files/mkdir"
    )
    try:
        target.mkdir(parents=True, exist_ok=True)
        if not target.exists() or not target.is_dir():
            raise RuntimeError("La carpeta no existe en disco tras mkdir.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"No se pudo crear la carpeta: {exc}")

    return {
        "confirmed": True,
        "status": "CREATED",
        "path": str(target)
    }


def delete_to_recycle_bin(raw_path: str) -> Dict[str, Any]:
    target = validate_authorized_path(
        raw_path,
        "allow_delete",
        "El permiso de eliminación a Papelera (allow_delete) está desactivado.",
        endpoint="/files/delete"
    )
    if not target.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"El archivo o carpeta '{target}' no existe.")

    _send_to_windows_recycle_bin(target)

    return {
        "confirmed": True,
        "status": "MOVED_TO_RECYCLE_BIN",
        "path": str(target),
        "removed_from_original_path": not target.exists(),
        "permanent_delete": False,
        "message": "El elemento fue enviado y verificado en la Papelera de Reciclaje de Windows."
    }


def move_or_rename(source_raw: str, destination_raw: str, overwrite: bool = False) -> Dict[str, Any]:
    src = validate_authorized_path(
        source_raw,
        "allow_file_move",
        "El permiso para mover o renombrar (allow_file_move) está desactivado.",
        endpoint="/files/move"
    )
    dst = validate_authorized_path(
        destination_raw,
        "allow_file_move",
        "El permiso para mover o renombrar (allow_file_move) está desactivado.",
        endpoint="/files/move"
    )

    if not src.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"La ruta origen '{src}' no existe.")

    backup_created = False
    backup_path = None

    if dst.exists():
        if not overwrite:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El destino '{dst}' ya existe y overwrite=False."
            )
        if dst.is_file():
            backup_path = _create_bak_backup(dst)
            backup_created = True

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        if not dst.exists() or src.exists():
            raise RuntimeError("No se pudo verificar el movimiento en disco.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error al mover/renombrar: {exc}")

    return {
        "confirmed": True,
        "status": "MOVED",
        "source_path": str(src),
        "destination_path": str(dst),
        "backup_created": backup_created,
        "backup_path": backup_path
    }


def search_files(
    base_raw: str,
    query: str,
    search_by_name: bool = True,
    search_by_content: bool = True,
    case_sensitive: bool = False,
    max_results: int = 100
) -> Dict[str, Any]:
    """
    Busca archivos dentro de una carpeta autorizada tanto por NOMBRE (o patrón glob)
    como por CONTENIDO interno en archivos de texto.
    """
    base_dir = validate_authorized_path(
        base_raw,
        "allow_file_search",
        "El permiso de búsqueda de archivos (allow_file_search) está desactivado.",
        endpoint="/files/search"
    )
    if not base_dir.exists() or not base_dir.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"La carpeta base '{base_dir}' no existe.")

    q_norm = query.strip() if case_sensitive else query.strip().lower()
    is_glob = any(ch in q_norm for ch in ("*", "?", "["))
    results: List[Dict[str, Any]] = []

    try:
        for root, dirs, files in os.walk(base_dir):
            root_path = Path(root)
            if is_forbidden_system_path(root_path):
                dirs[:] = []
                continue

            for entry_name in dirs + files:
                full_p = root_path / entry_name
                if is_forbidden_system_path(full_p):
                    continue

                cmp_name = entry_name if case_sensitive else entry_name.lower()
                name_matched = False
                if search_by_name:
                    name_matched = fnmatch.fnmatch(cmp_name, q_norm) if is_glob else (q_norm in cmp_name)

                content_snippets: List[Dict[str, Any]] = []
                if search_by_content and not is_glob and full_p.is_file():
                    ext = full_p.suffix.lower()
                    try:
                        st_size = full_p.stat().st_size
                        if (ext in TEXT_SEARCH_EXTENSIONS or st_size <= 262144) and st_size <= 2 * 1024 * 1024:
                            with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                                for idx, line in enumerate(f, start=1):
                                    cmp_line = line if case_sensitive else line.lower()
                                    if q_norm in cmp_line:
                                        content_snippets.append({
                                            "line": idx,
                                            "preview": line.strip()[:180]
                                        })
                                        if len(content_snippets) >= 5:
                                            break
                    except Exception:
                        pass

                if name_matched or content_snippets:
                    item_info = _format_item(full_p)
                    item_info["matched_by_name"] = name_matched
                    item_info["matched_by_content"] = bool(content_snippets)
                    item_info["content_matches"] = content_snippets
                    results.append(item_info)
                    if len(results) >= max_results:
                        return {
                            "confirmed": True,
                            "base_path": str(base_dir),
                            "query": query,
                            "total_matches": len(results),
                            "results": results
                        }
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error en búsqueda: {exc}")

    return {
        "confirmed": True,
        "base_path": str(base_dir),
        "query": query,
        "total_matches": len(results),
        "results": results
    }


def upload_file_base64(destination_raw: str, base64_data: str) -> Dict[str, Any]:
    target = validate_authorized_path(
        destination_raw,
        "allow_file_transfer",
        "El permiso para subir o descargar archivos (allow_file_transfer) está desactivado.",
        endpoint="/files/upload"
    )
    if target.exists() and target.is_dir():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La ruta destino es una carpeta.")

    try:
        raw_bytes = base64.b64decode(base64_data, validate=True)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El contenido Base64 proporcionado es inválido.")

    backup_created = False
    backup_path = None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_file():
            backup_path = _create_bak_backup(target)
            backup_created = True

        with open(target, "wb") as f:
            f.write(raw_bytes)
            f.flush()
            os.fsync(f.fileno())

        if not target.exists() or target.stat().st_size != len(raw_bytes):
            raise RuntimeError("No se pudo verificar el tamaño del archivo subido en disco.")
        file_hash = _compute_sha256(target)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error al guardar archivo subido: {exc}")

    return {
        "confirmed": True,
        "status": "UPLOADED",
        "path": str(target),
        "size_bytes": len(raw_bytes),
        "sha256": file_hash,
        "backup_created": backup_created,
        "backup_path": backup_path
    }


def download_file_base64(raw_path: str) -> Dict[str, Any]:
    target = validate_authorized_path(
        raw_path,
        "allow_file_transfer",
        "El permiso para subir o descargar archivos (allow_file_transfer) está desactivado.",
        endpoint="/files/download"
    )
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"El archivo '{target}' no existe.")

    size = target.stat().st_size
    if size > 25 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="El archivo supera el límite de 25 MB para descarga directa.")

    try:
        with open(target, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("ascii")
        file_hash = hashlib.sha256(raw).hexdigest()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error al codificar archivo: {exc}")

    mime, _ = mimetypes.guess_type(str(target))
    return {
        "confirmed": True,
        "path": str(target),
        "filename": target.name,
        "size_bytes": size,
        "sha256": file_hash,
        "mime_type": mime or "application/octet-stream",
        "base64_data": b64
    }
