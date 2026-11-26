"""
Servicio Interno de Salud y Auto-Recuperación del Puente.
Registra el estado de salud en health_state.json, verifica periódicamente que el túnel
Tailscale Funnel (:8443) esté activo apuntando al puerto local configurado (siempre que el Kill Switch esté desactivado).
"""

import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any
import psutil

from app.core.permissions import load_permissions, PROJECT_ROOT
from app.core.port_utils import get_configured_local_port
from app.core.logger import log_audit_event
from app.services.pc_service import BRIDGE_START_TIME

HEALTH_STATE_FILE = PROJECT_ROOT / "health_state.json"
BRIDGE_VERSION = "2.1.0"
FUNNEL_PORT = 8443


def _run_hidden(cmd, timeout=5):
    kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000
    return subprocess.run(cmd, **kwargs)


def check_funnel_8443_status() -> bool:
    try:
        res = _run_hidden(["tailscale", "funnel", "status"], timeout=4)
        out = res.stdout or ""
        return f":{FUNNEL_PORT}" in out and "Funnel on" in out
    except Exception:
        return False


def check_local_port_listening(port: int = None) -> bool:
    target_port = port if port is not None else get_configured_local_port()
    try:
        with socket.create_connection(("127.0.0.1", int(target_port)), timeout=0.5):
            return True
    except Exception:
        return False


def load_health_state() -> Dict[str, Any]:
    if HEALTH_STATE_FILE.exists():
        try:
            with open(HEALTH_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "bridge_version": BRIDGE_VERSION,
        "local_port": get_configured_local_port(),
        "last_check_timestamp": None,
        "last_check_local": "Sin chequeo registrado",
        "server_healthy": True,
        "funnel_8443_active": True,
        "recovery_count": 0,
        "last_recovery_event": None
    }


def save_health_state(state: Dict[str, Any]) -> None:
    try:
        tmp = HEALTH_STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, HEALTH_STATE_FILE)
    except Exception:
        pass


def perform_health_check(auto_recover_funnel: bool = True, source: str = "internal_monitor") -> Dict[str, Any]:
    """
    Ejecuta un chequeo completo de salud del servidor, métricas de CPU/RAM,
    estado de permisos y Tailscale Funnel 8443 apuntando al puerto local configurado.
    """
    prev_state = load_health_state()
    perms = load_permissions()
    local_port = get_configured_local_port()
    kill_switch = bool(perms.get("kill_switch_active", False))

    now_utc = datetime.now(timezone.utc).isoformat()
    now_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    funnel_ok = check_funnel_8443_status()
    recovery_count = int(prev_state.get("recovery_count", 0))
    last_recovery_event = prev_state.get("last_recovery_event")

    if not kill_switch and not funnel_ok and auto_recover_funnel:
        try:
            _run_hidden(
                ["tailscale", "funnel", "--bg", f"--https={FUNNEL_PORT}", f"http://127.0.0.1:{local_port}"],
                timeout=8
            )
            time.sleep(0.5)
            funnel_ok = check_funnel_8443_status()
            if funnel_ok:
                recovery_count += 1
                last_recovery_event = f"{now_local} — Funnel :8443 restaurado hacia :{local_port} ({source})"
                log_audit_event(
                    client_ip=source,
                    endpoint="/health",
                    action="AUTO_RECOVERY_FUNNEL_8443",
                    details={"funnel_port": FUNNEL_PORT, "local_port": local_port, "recovery_count": recovery_count},
                    status="SUCCESS"
                )
        except Exception as exc:
            log_audit_event(
                client_ip=source,
                endpoint="/health",
                action="AUTO_RECOVERY_FUNNEL_FAILED",
                details={"error": str(exc)},
                status="ERROR"
            )

    vm = psutil.virtual_memory()
    cpu_pct = psutil.cpu_percent(interval=0.05)
    bridge_uptime = round(time.time() - BRIDGE_START_TIME, 1)
    os_uptime = round(time.time() - psutil.boot_time(), 1)

    allowed_apps = perms.get("allowed_apps", {})
    catalog = perms.get("installed_apps_catalog", {})
    auth_apps_count = sum(1 for k in catalog if allowed_apps.get(k, False))

    file_perm_keys = [
        "allow_file_list", "allow_file_read", "allow_file_write",
        "allow_file_mkdir", "allow_delete", "allow_file_move",
        "allow_file_search", "allow_file_transfer"
    ]
    active_file_perms = sum(1 for k in file_perm_keys if perms.get(k, False))

    state = {
        "confirmed": True,
        "status": "KILLED_BY_EMERGENCY_SWITCH" if kill_switch else "HEALTHY",
        "bridge_version": BRIDGE_VERSION,
        "local_port": local_port,
        "funnel_port": FUNNEL_PORT,
        "last_check_timestamp": now_utc,
        "last_check_local": now_local,
        "server_healthy": not kill_switch,
        "funnel_8443_active": funnel_ok,
        "kill_switch_active": kill_switch,
        "bridge_uptime_seconds": bridge_uptime,
        "os_uptime_seconds": os_uptime,
        "cpu_percent": cpu_pct,
        "memory_percent": vm.percent,
        "memory_used_gb": round(vm.used / (1024 ** 3), 2),
        "memory_total_gb": round(vm.total / (1024 ** 3), 2),
        "permissions_summary": {
            "allow_screenshot": bool(perms.get("allow_screenshot", False)),
            "allow_whatsapp": bool(perms.get("allow_whatsapp", False)),
            "allow_gui_control": bool(perms.get("allow_gui_control", False)),
            "allow_open_app": bool(perms.get("allow_open_app", False)),
            "authorized_apps_count": auth_apps_count,
            "total_scanned_apps": len(catalog),
            "active_file_permissions": active_file_perms,
            "authorized_paths_count": len(perms.get("allowed_paths", []))
        },
        "recovery_count": recovery_count,
        "last_recovery_event": last_recovery_event
    }
    save_health_state(state)
    return state
