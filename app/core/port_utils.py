"""
Utilidades de Gestión de Puertos Locales para MuseBridge.
- Detecta si un puerto TCP está Libre u Ocupado e identifica el proceso (PID y nombre).
- Distingue si el proceso que ocupa el puerto es el propio MuseBridge o una aplicación externa (Error 10048).
- Escanea rangos de puertos (ej. 8000–8010) listando libres y ocupados.
- Lee y guarda de forma persistente el puerto local elegido en permissions.json y .env.
"""

import json
import os
import re
import socket
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

try:
    import psutil
except ImportError:
    psutil = None

from app.core.permissions import PROJECT_ROOT, PERMISSIONS_FILE, load_permissions, save_permissions

ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_LOCAL_PORT = 8000
PUBLIC_FUNNEL_PORT = 8443


def get_configured_local_port() -> int:
    """
    Obtiene el puerto local guardado en la configuración (permissions.json o .env).
    Si no existe, devuelve 8000 por defecto.
    """
    try:
        if PERMISSIONS_FILE.exists():
            with open(PERMISSIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                p = data.get("local_port")
                if isinstance(p, int) and 1024 <= p <= 65535:
                    return p
    except Exception:
        pass

    try:
        if ENV_FILE.exists():
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("SERVER_PORT="):
                        val = int(line.strip().split("=", 1)[1].strip())
                        if 1024 <= val <= 65535:
                            return val
    except Exception:
        pass

    return DEFAULT_LOCAL_PORT


def save_configured_local_port(new_port: int, target_root: Optional[Path] = None) -> None:
    """
    Guarda el puerto local elegido tanto en permissions.json como en .env
    para que el servidor, el panel y el watchdog lo usen en todos los arranques siguientes.
    """
    port_val = int(new_port)
    if not (1024 <= port_val <= 65535):
        raise ValueError(f"El puerto {port_val} debe estar entre 1024 y 65535.")

    root_dir = (target_root or PROJECT_ROOT).resolve()
    perms_file = root_dir / "permissions.json"
    env_file = root_dir / ".env"

    # 1. Guardar en permissions.json
    if root_dir == PROJECT_ROOT.resolve():
        perms = load_permissions()
        perms["local_port"] = port_val
        save_permissions(perms)
    else:
        data = {}
        if perms_file.exists():
            try:
                with open(perms_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["local_port"] = port_val
        tmp = perms_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, perms_file)

    # 2. Guardar en .env (SERVER_PORT=...)
    lines = []
    port_written = False
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("SERVER_PORT="):
                    lines.append(f"SERVER_PORT={port_val}\n")
                    port_written = True
                else:
                    lines.append(line)
    if not port_written:
        lines.append(f"SERVER_PORT={port_val}\n")

    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(lines)


def is_port_bound(port: int, host: str = "127.0.0.1") -> bool:
    """Comprueba si un puerto TCP está ocupado (alguien escucha o no permite bind)."""
    try:
        with socket.create_connection((host, int(port)), timeout=0.25):
            return True
    except Exception:
        pass

    # Comprobar también intentando hacer bind sin SO_REUSEADDR
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, int(port)))
        return False
    except OSError:
        return True
    finally:
        try:
            s.close()
        except Exception:
            pass


def is_musebridge_responding_on_port(port: int, timeout: float = 0.8) -> bool:
    """Verifica si quien responde en 127.0.0.1:<port>/health es realmente el servidor MuseBridge."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{int(port)}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                return "bridge_version" in data and data.get("status") in ("HEALTHY", "KILLED_BY_EMERGENCY_SWITCH")
    except Exception:
        pass
    return False


def inspect_port(port: int, check_http_bridge: bool = True) -> Dict[str, Any]:
    """
    Inspecciona el estado de un puerto local:
    - 'Libre' o 'Ocupado'
    - PID y nombre del proceso que lo ocupa (si está disponible vía psutil)
    - Si pertenece al propio servidor MuseBridge (is_own_bridge) o a un proceso externo.
    """
    port_num = int(port)
    pid: Optional[int] = None
    proc_name: Optional[str] = None
    cmdline_str: str = ""

    if psutil:
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == port_num and conn.status == "LISTEN":
                    pid = conn.pid
                    if pid:
                        try:
                            p = psutil.Process(pid)
                            proc_name = p.name()
                            cmdline_str = " ".join(p.cmdline())
                        except Exception:
                            proc_name = "Proceso del sistema"
                    break
        except Exception:
            pass

    bound = (pid is not None) or is_port_bound(port_num)
    if not bound:
        return {
            "port": port_num,
            "in_use": False,
            "status": "Libre",
            "pid": None,
            "process_name": None,
            "is_own_bridge": False,
            "owner_description": "Libre (Disponible para usar)"
        }

    is_own = False
    if "app.main:app" in cmdline_str or "run_server.py" in cmdline_str:
        is_own = True
    elif check_http_bridge and is_musebridge_responding_on_port(port_num, timeout=0.6):
        is_own = True

    if is_own:
        pid_txt = f"PID {pid}" if pid else "Activo"
        p_txt = f"{proc_name} · " if proc_name else ""
        desc = f"En uso por MuseBridge ({p_txt}{pid_txt})"
    else:
        if proc_name and pid:
            desc = f"Ocupado por {proc_name} (PID {pid})"
        elif pid:
            desc = f"Ocupado por PID {pid}"
        else:
            desc = "Ocupado por otro proceso del sistema"

    return {
        "port": port_num,
        "in_use": True,
        "status": "Ocupado",
        "pid": pid,
        "process_name": proc_name,
        "is_own_bridge": is_own,
        "owner_description": desc
    }


def scan_port_range(start_port: int = 8000, end_port: int = 8010) -> List[Dict[str, Any]]:
    """
    Escanea un rango de puertos (por defecto 8000–8010) de forma rápida
    y devuelve la lista completa con estado Libre/Ocupado y qué proceso ocupa cada uno.
    """
    start_p = max(1024, min(65535, int(start_port)))
    end_p = max(start_p, min(65535, int(end_port)))
    if end_p - start_p > 100:
        end_p = start_p + 100

    # Mapear todas las conexiones LISTEN de una sola vez con psutil para máxima velocidad
    listen_map: Dict[int, Tuple[Optional[int], Optional[str], str]] = {}
    if psutil:
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.status == "LISTEN" and start_p <= conn.laddr.port <= end_p:
                    p_num = conn.laddr.port
                    pid = conn.pid
                    p_name = None
                    cmd_s = ""
                    if pid:
                        try:
                            pr = psutil.Process(pid)
                            p_name = pr.name()
                            cmd_s = " ".join(pr.cmdline())
                        except Exception:
                            p_name = "Proceso del sistema"
                    listen_map[p_num] = (pid, p_name, cmd_s)
        except Exception:
            pass

    results: List[Dict[str, Any]] = []
    for p_num in range(start_p, end_p + 1):
        if p_num in listen_map:
            pid, p_name, cmd_s = listen_map[p_num]
            is_own = ("app.main:app" in cmd_s) or ("run_server.py" in cmd_s) or is_musebridge_responding_on_port(p_num, timeout=0.35)
            if is_own:
                desc = f"En uso por MuseBridge ({p_name or 'python'} · PID {pid})"
            elif p_name and pid:
                desc = f"Ocupado por {p_name} (PID {pid})"
            else:
                desc = f"Ocupado (PID {pid or 'desconocido'})"
            results.append({
                "port": p_num,
                "in_use": True,
                "status": "Ocupado",
                "pid": pid,
                "process_name": p_name,
                "is_own_bridge": is_own,
                "owner_description": desc
            })
        else:
            bound = is_port_bound(p_num)
            if bound:
                is_own = is_musebridge_responding_on_port(p_num, timeout=0.35)
                results.append({
                    "port": p_num,
                    "in_use": True,
                    "status": "Ocupado",
                    "pid": None,
                    "process_name": None,
                    "is_own_bridge": is_own,
                    "owner_description": "En uso por MuseBridge" if is_own else "Ocupado por otro proceso"
                })
            else:
                results.append({
                    "port": p_num,
                    "in_use": False,
                    "status": "Libre",
                    "pid": None,
                    "process_name": None,
                    "is_own_bridge": False,
                    "owner_description": "Libre (Disponible para usar)"
                })
    return results


def validate_port_selection(target_port: int) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Valida si el usuario puede elegir y aplicar target_port.
    Si el puerto está ocupado por otro proceso externo, devuelve (False, mensaje_error, info).
    """
    try:
        p_num = int(target_port)
    except (ValueError, TypeError):
        return False, "El puerto ingresado debe ser un número entero válido.", {}

    if not (1024 <= p_num <= 65535):
        return False, f"El puerto {p_num} está fuera del rango permitido (1024–65535).", {}

    if p_num == PUBLIC_FUNNEL_PORT:
        return False, f"El puerto {PUBLIC_FUNNEL_PORT} está reservado para el túnel público de Tailscale Funnel. Elige otro puerto local (ej. 8000–8010).", {}

    info = inspect_port(p_num, check_http_bridge=True)
    if info["in_use"] and not info["is_own_bridge"]:
        return (
            False,
            f"No se puede aplicar el puerto {p_num} porque está OCUPADO (Error 10048).\n\n"
            f"Detalle: {info['owner_description']}.\n\n"
            f"Por favor selecciona un puerto con estado 'Libre' en la lista.",
            info
        )

    return True, "Puerto disponible.", info
