"""
Módulo Central de Permisos (RBAC), Sandboxing de Rutas y Kill Switch.
Política de Seguridad: Denegado por Defecto (Default-Deny).
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional


def _resolve_project_root() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name.lower() == "dist" and (exe_dir.parent / "permissions.json").exists():
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _resolve_project_root()
PERMISSIONS_FILE = PROJECT_ROOT / "permissions.json"

# Carpetas críticas del sistema operativo y del propio puente que NUNCA pueden ser accedidas por la API de archivos
FORBIDDEN_SYSTEM_PREFIXES = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve(),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")).resolve(),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")).resolve(),
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")).resolve(),
    Path(r"C:\$Recycle.Bin").resolve(),
    Path(r"C:\System Volume Information").resolve(),
    Path(r"C:\Recovery").resolve(),
    PROJECT_ROOT.resolve(),  # Protege .env, permissions.json, audit.log y binarios del puente
]

DEFAULT_PERMISSIONS: Dict[str, Any] = {
    "kill_switch_active": False,
    "local_port": 8000,
    # Permisos generales del agente
    "allow_screenshot": False,
    "allow_whatsapp": False,
    "allow_gui_control": False,
    "allow_open_app": False,
    # Permisos granulares de archivos (todos en False por defecto)
    "allow_file_list": False,
    "allow_file_read": False,
    "allow_file_write": False,
    "allow_file_mkdir": False,
    "allow_delete": False,
    "allow_file_move": False,
    "allow_file_search": False,
    "allow_file_transfer": False,
    # Rutas autorizadas por el usuario (vacío por defecto)
    "allowed_paths": [],
    # Aplicaciones autorizadas (todas en False por defecto)
    "allowed_apps": {},
    # Catálogo escaneado de aplicaciones instaladas en el PC
    "installed_apps_catalog": {}
}


def load_permissions() -> Dict[str, Any]:
    if not PERMISSIONS_FILE.exists():
        save_permissions(DEFAULT_PERMISSIONS)
        return json.loads(json.dumps(DEFAULT_PERMISSIONS))
    try:
        with open(PERMISSIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in DEFAULT_PERMISSIONS.items():
                if k not in data:
                    data[k] = json.loads(json.dumps(v))
                elif isinstance(v, dict) and not isinstance(data[k], dict):
                    data[k] = json.loads(json.dumps(v))
                elif isinstance(v, list) and not isinstance(data[k], list):
                    data[k] = json.loads(json.dumps(v))
            return data
    except Exception:
        return json.loads(json.dumps(DEFAULT_PERMISSIONS))


def save_permissions(data: Dict[str, Any]) -> None:
    temp_file = PERMISSIONS_FILE.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(temp_file, PERMISSIONS_FILE)


def sync_scanned_apps(scanned_catalog: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Sincroniza las aplicaciones detectadas en el PC con permissions.json.
    Toda nueva aplicación se registra con permiso False (denegada por defecto).
    """
    perms = load_permissions()
    allowed_apps = perms.get("allowed_apps", {})
    for app_id in scanned_catalog:
        if app_id not in allowed_apps:
            allowed_apps[app_id] = False
    perms["allowed_apps"] = allowed_apps
    perms["installed_apps_catalog"] = scanned_catalog
    save_permissions(perms)
    return perms


def _audit(endpoint: str, action: str, details: Dict[str, Any], status_str: str = "FORBIDDEN"):
    try:
        from app.core.logger import log_audit_event
        log_audit_event(
            client_ip="security_guard",
            endpoint=endpoint,
            action=action,
            details=details,
            status=status_str
        )
    except Exception:
        pass


def check_kill_switch():
    perms = load_permissions()
    if perms.get("kill_switch_active", False):
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="EMERGENCIA: Conexión cortada por el botón de parada de emergencia (Kill Switch). "
                   "La conexión solo puede ser restablecida manualmente por el usuario desde el panel físico del PC."
        )


def check_permission(permission_key: str, error_message: str):
    check_kill_switch()
    perms = load_permissions()
    if not perms.get(permission_key, False):
        _audit(permission_key, "PERMISSION_DENIED", {"required_permission": permission_key})
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permiso denegado ({permission_key}): {error_message}"
        )


def resolve_authorized_app(app_query: str) -> Dict[str, Any]:
    """
    Verifica que el permiso global allow_open_app esté activo y que la aplicación
    solicitada exista en el catálogo y esté marcada como permitida (True) por el usuario.
    Devuelve el registro de la aplicación si está autorizada.
    """
    check_permission(
        "allow_open_app",
        "La apertura de aplicaciones está desactivada en el panel de control."
    )
    perms = load_permissions()
    allowed_apps: Dict[str, bool] = perms.get("allowed_apps", {})
    catalog: Dict[str, Dict[str, Any]] = perms.get("installed_apps_catalog", {})

    clean_query = app_query.strip().lower()
    matched_id: Optional[str] = None

    # 1. Búsqueda exacta por ID
    if clean_query in allowed_apps or clean_query in catalog:
        matched_id = clean_query
    else:
        # 2. Búsqueda por nombre visible (case-insensitive)
        for app_id, info in catalog.items():
            if info.get("name", "").strip().lower() == clean_query:
                matched_id = app_id
                break
        # 3. Búsqueda parcial si coincide de forma única con una app autorizada
        if not matched_id:
            for app_id, info in catalog.items():
                if clean_query in app_id or clean_query in info.get("name", "").lower():
                    if allowed_apps.get(app_id, False):
                        matched_id = app_id
                        break

    if not matched_id or not allowed_apps.get(matched_id, False):
        _audit(
            "/pc/open-app",
            "APP_PERMISSION_DENIED",
            {"requested_app": app_query, "matched_id": matched_id}
        )
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permiso denegado: La aplicación '{app_query}' no está autorizada por el usuario en el panel."
        )

    app_info = catalog.get(matched_id, {"id": matched_id, "name": matched_id, "path": matched_id})
    return app_info


def check_app_permission(app_name: str):
    """Mantiene compatibilidad con validaciones previas."""
    return resolve_authorized_app(app_name)


def is_forbidden_system_path(target_path: Path) -> bool:
    """
    Verifica si una ruta cae dentro de carpetas críticas del sistema Windows o del propio puente.
    También bloquea la raíz de cualquier disco (ej. C:\\).
    """
    resolved = target_path.resolve()

    # Bloquear raíz directa de unidades (ej. C:\)
    if resolved == Path(resolved.anchor):
        return True

    for forbidden in FORBIDDEN_SYSTEM_PREFIXES:
        try:
            if resolved == forbidden or resolved.is_relative_to(forbidden):
                return True
        except Exception:
            continue

    # Bloquear AppData\Local\Microsoft, WindowsApps, System32, etc.
    low_str = str(resolved).lower()
    dangerous_fragments = (
        "\\windows\\",
        "\\system32",
        "\\syswow64",
        "\\program files",
        "\\programdata\\",
        "\\$recycle.bin",
        "\\system volume information",
        "\\appdata\\local\\microsoft\\windowsapps",
    )
    for frag in dangerous_fragments:
        if frag in low_str:
            return True

    return False


def validate_authorized_path(raw_path: str, permission_key: str, error_message: str, endpoint: str = "/files") -> Path:
    """
    1. Verifica Kill Switch.
    2. Verifica que el permiso específico de archivo (ej. allow_file_read) esté en True.
    3. Verifica que la ruta no sea una carpeta prohibida del sistema.
    4. Verifica que la ruta esté estrictamente contenida dentro de una de las carpetas
       autorizadas por el usuario en allowed_paths.
    """
    check_permission(permission_key, error_message)

    if not raw_path or not raw_path.strip():
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La ruta proporcionada está vacía."
        )

    try:
        target = Path(os.path.expandvars(raw_path.strip())).resolve()
    except Exception as exc:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Formato de ruta inválido: {exc}"
        )

    if is_forbidden_system_path(target):
        _audit(
            endpoint,
            "SYSTEM_PATH_BLOCKED",
            {"requested_path": str(target), "permission": permission_key}
        )
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Acceso bloqueado por seguridad: No se permite operar sobre carpetas del sistema ({target})."
        )

    perms = load_permissions()
    allowed_paths_raw: List[str] = perms.get("allowed_paths", [])
    if not allowed_paths_raw:
        _audit(
            endpoint,
            "NO_AUTHORIZED_PATHS",
            {"requested_path": str(target), "permission": permission_key}
        )
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permiso denegado: No hay ninguna carpeta autorizada en el panel de control."
        )

    is_inside_allowed = False
    for allowed_str in allowed_paths_raw:
        try:
            allowed_dir = Path(os.path.expandvars(allowed_str)).resolve()
            if is_forbidden_system_path(allowed_dir):
                continue
            if target == allowed_dir or target.is_relative_to(allowed_dir):
                is_inside_allowed = True
                break
        except Exception:
            continue

    if not is_inside_allowed:
        _audit(
            endpoint,
            "PATH_OUTSIDE_SANDBOX",
            {"requested_path": str(target), "allowed_paths": allowed_paths_raw}
        )
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permiso denegado: La ruta '{target}' está fuera de las carpetas autorizadas por el usuario."
        )

    return target
