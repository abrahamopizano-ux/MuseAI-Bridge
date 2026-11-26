r"""
MUSE AI PC BRIDGE — SERVICIO VIGILANTE (WATCHDOG) Y AUTO-RECUPERACIÓN (v2.1.0)
Reglas de Seguridad y Estabilidad en Producción:
1. Lee en cada ciclo el puerto local guardado en permissions.json / .env (get_configured_local_port()).
2. Antes de hacer nada, comprueba si GET http://127.0.0.1:<local_port>/health ya responde 200 OK.
   Si responde 200 OK, NO hace nada.
3. NUNCA mata ni detiene procesos que no haya lanzado él mismo. Si el puerto local ya está
   ocupado por otro proceso externo, registra el conflicto y NO intenta arrancar en bucle.
4. Límite estricto de máximo 3 reinicios por hora (ventana deslizante de 3600 s).
5. Arranque en producción SIN --reload (usa run_server.py con redirección segura de stdio a server.log).
"""

import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.port_utils import get_configured_local_port

PERMISSIONS_FILE = PROJECT_ROOT / "permissions.json"
HEALTH_STATE_FILE = PROJECT_ROOT / "health_state.json"
AUDIT_LOG_FILE = PROJECT_ROOT / "audit.log"
SERVER_LOG_FILE = PROJECT_ROOT / "server.log"

VENV_PYTHONW = PROJECT_ROOT / "venv" / "Scripts" / "pythonw.exe"
VENV_PYTHON = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"

FUNNEL_PORT = 8443
TASK_NAME = "MuseAIBridgeWatchdog"
MAX_RESTARTS_PER_HOUR = 3
HOUR_SECONDS = 3600.0


def run_hidden(cmd, timeout=8):
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


def is_kill_switch_active() -> bool:
    if not PERMISSIONS_FILE.exists():
        return False
    try:
        with open(PERMISSIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return bool(data.get("kill_switch_active", False))
    except Exception:
        return False


def is_health_200(port: int, retries: int = 2, timeout: float = 3.5) -> bool:
    """Comprueba si http://127.0.0.1:<port>/health responde HTTP 200."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{int(port)}/health", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            if attempt + 1 < retries:
                time.sleep(1.0)
    return False


def is_port_in_use(port: int) -> bool:
    """Comprueba si el puerto TCP ya está ocupado por algún proceso."""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1.0):
            return True
    except Exception:
        return False


def is_funnel_8443_pointing_to(local_port: int) -> bool:
    try:
        res = run_hidden(["tailscale", "funnel", "status"], timeout=5)
        out = res.stdout or ""
        return f":{FUNNEL_PORT}" in out and "Funnel on" in out and f"127.0.0.1:{int(local_port)}" in out
    except Exception:
        return False


def append_audit_record(action: str, details: dict, status_str: str = "SUCCESS"):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "client_ip": "watchdog_service",
        "endpoint": "/watchdog",
        "action": action,
        "status": status_str,
        "details": details
    }
    try:
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_health_state() -> dict:
    if HEALTH_STATE_FILE.exists():
        try:
            with open(HEALTH_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def update_health_file(
    local_port: int,
    server_ok: bool,
    funnel_ok: bool,
    restart_timestamps: List[float],
    circuit_breaker: bool = False,
    port_conflict: bool = False,
    recovery_event: Optional[str] = None
):
    state = load_health_state()
    now_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["bridge_version"] = "2.1.0"
    state["local_port"] = int(local_port)
    state["funnel_port"] = FUNNEL_PORT
    state["last_check_timestamp"] = datetime.now(timezone.utc).isoformat()
    state["last_check_local"] = now_local
    state["server_healthy"] = server_ok
    state["funnel_8443_active"] = funnel_ok
    state["watchdog_active"] = True
    state["port_conflict_detected"] = port_conflict
    state["restarts_last_hour"] = len(restart_timestamps)
    state["max_restarts_per_hour"] = MAX_RESTARTS_PER_HOUR
    state["restart_timestamps_last_hour"] = restart_timestamps
    state["circuit_breaker_tripped"] = circuit_breaker

    if recovery_event:
        state["recovery_count"] = int(state.get("recovery_count", 0)) + 1
        state["last_recovery_event"] = f"{now_local} — {recovery_event}"

    try:
        tmp = HEALTH_STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, HEALTH_STATE_FILE)
    except Exception:
        pass


def start_uvicorn_server_production(local_port: int) -> Optional[subprocess.Popen]:
    """
    Arranca el servidor en el puerto guardado en modo producción (SIN --reload)
    redirigiendo stdout/stderr a server.log.
    """
    py_exe = str(VENV_PYTHONW) if VENV_PYTHONW.exists() else (str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable)
    run_server_script = PROJECT_ROOT / "run_server.py"

    if run_server_script.exists():
        cmd = [py_exe, str(run_server_script), "--host", "127.0.0.1", "--port", str(int(local_port))]
    else:
        cmd = [
            py_exe,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(int(local_port)),
            "--no-use-colors"
        ]

    log_fh = open(SERVER_LOG_FILE, "a", encoding="utf-8", buffering=1)
    kwargs = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": log_fh,
        "stderr": log_fh,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000
    return subprocess.Popen(cmd, **kwargs)


def restore_funnel_8443(local_port: int):
    run_hidden(
        ["tailscale", "funnel", "--bg", f"--https={FUNNEL_PORT}", f"http://127.0.0.1:{int(local_port)}"],
        timeout=8
    )


def register_resilient_autostart(install_root: Optional[Path] = None) -> dict:
    """
    Configura todos los launchers (.bat, .vbs, Tarea Programada y clave HKCU Run)
    apuntando exclusivamente a la carpeta de instalación indicada (install_root),
    sin rutas viejas y sin --reload.
    """
    target_root = (install_root or PROJECT_ROOT).resolve()
    venv_pyw = target_root / "venv" / "Scripts" / "pythonw.exe"
    venv_py = target_root / "venv" / "Scripts" / "python.exe"
    py_exe = str(venv_pyw) if venv_pyw.exists() else (str(venv_py) if venv_py.exists() else sys.executable)
    watchdog_script = str(target_root / "watchdog_service.py")
    run_server_script = str(target_root / "run_server.py")

    # 1. iniciar-puente.bat en la carpeta de instalación (sin --reload, run_server.py lee el puerto guardado)
    bat_path = target_root / "iniciar-puente.bat"
    bat_content = (
        '@echo off\r\n'
        f'cd /d "{target_root}"\r\n'
        f'if exist "{venv_pyw}" (\r\n'
        f'    start "" "{venv_pyw}" "{watchdog_script}"\r\n'
        ') else (\r\n'
        f'    "{venv_py}" "{run_server_script}" --host 127.0.0.1\r\n'
        ')\r\n'
    )
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    # 2. iniciar-puente.vbs en shell:startup apuntando a target_root
    startup_dir = Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"))
    startup_vbs = startup_dir / "iniciar-puente.vbs"
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\r\n'
        f'WshShell.CurrentDirectory = "{target_root}"\r\n'
        f'WshShell.Run """{py_exe}"" ""{watchdog_script}""", 0, False\r\n'
    )
    if startup_dir.exists():
        with open(startup_vbs, "w", encoding="utf-8") as f:
            f.write(vbs_content)

    # 3. Registro HKCU\Software\Microsoft\Windows\CurrentVersion\Run apuntando a target_root
    reg_registered = False
    if os.name == "nt":
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as reg_key:
                winreg.SetValueEx(reg_key, TASK_NAME, 0, winreg.REG_SZ, f'"{py_exe}" "{watchdog_script}"')
            reg_registered = True
        except Exception:
            pass

    # 4. Tarea Programada cada 5 minutos apuntando a target_root
    task_cmd = f'"{py_exe}" "{watchdog_script}"'
    res = run_hidden(
        [
            "schtasks",
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            task_cmd,
            "/SC",
            "MINUTE",
            "/MO",
            "5",
            "/F"
        ],
        timeout=8
    )

    return {
        "confirmed": True,
        "install_root": str(target_root),
        "python_exe": py_exe,
        "bat_path": str(bat_path),
        "startup_vbs": str(startup_vbs),
        "registry_run_registered": reg_registered,
        "scheduled_task_registered": res.returncode == 0,
        "task_name": TASK_NAME
    }


def run_watchdog_loop():
    if os.name == "nt":
        mutex_name = "Local\\MuseAIBridgeWatchdogMutex_v21"
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, mutex_name)
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return
        _ = mutex

    prev_state = load_health_state()
    now_init = time.time()
    restart_timestamps: List[float] = [
        float(ts) for ts in prev_state.get("restart_timestamps_last_hour", [])
        if now_init - float(ts) < HOUR_SECONDS
    ]
    circuit_breaker_logged = False
    last_funnel_check = 0.0
    funnel_ok = True

    while True:
        try:
            now = time.time()
            local_port = get_configured_local_port()
            restart_timestamps = [ts for ts in restart_timestamps if now - ts < HOUR_SECONDS]

            if is_kill_switch_active():
                update_health_file(
                    local_port=local_port,
                    server_ok=False,
                    funnel_ok=False,
                    restart_timestamps=restart_timestamps,
                    circuit_breaker=False
                )
                time.sleep(20)
                continue

            # 1. Comprobar primero si /health en el puerto configurado ya responde 200 OK
            srv_ok = is_health_200(port=local_port, retries=2, timeout=3.5)
            recovery_msg = None

            if srv_ok:
                circuit_breaker_logged = False
            else:
                # Si /health no responde pero el puerto ya está ocupado por un proceso externo (Error 10048),
                # NO matar el proceso ajeno ni intentar arrancar en bucle.
                if is_port_in_use(local_port):
                    update_health_file(
                        local_port=local_port,
                        server_ok=False,
                        funnel_ok=funnel_ok,
                        restart_timestamps=restart_timestamps,
                        circuit_breaker=False,
                        port_conflict=True
                    )
                    time.sleep(30)
                    continue

                if len(restart_timestamps) >= MAX_RESTARTS_PER_HOUR:
                    if not circuit_breaker_logged:
                        circuit_breaker_logged = True
                        append_audit_record(
                            "WATCHDOG_MAX_RESTARTS_EXCEEDED",
                            {
                                "port": local_port,
                                "restarts_in_last_hour": len(restart_timestamps),
                                "max_allowed": MAX_RESTARTS_PER_HOUR,
                                "action": "PAUSED_UNTIL_WINDOW_CLEARS"
                            },
                            status_str="ERROR"
                        )
                    update_health_file(
                        local_port=local_port,
                        server_ok=False,
                        funnel_ok=funnel_ok,
                        restart_timestamps=restart_timestamps,
                        circuit_breaker=True
                    )
                    time.sleep(60)
                    continue

                restart_timestamps.append(time.time())
                start_uvicorn_server_production(local_port=local_port)

                for _ in range(12):
                    time.sleep(1.0)
                    if is_health_200(port=local_port, retries=1, timeout=2.0):
                        srv_ok = True
                        break

                recovery_msg = f"Servidor Uvicorn (:{local_port}) iniciado por Watchdog (intento {len(restart_timestamps)}/{MAX_RESTARTS_PER_HOUR} en la última hora)"
                append_audit_record(
                    "WATCHDOG_SERVER_STARTED",
                    {
                        "port": local_port,
                        "recovered_health_200": srv_ok,
                        "restarts_in_last_hour": len(restart_timestamps)
                    },
                    status_str="SUCCESS" if srv_ok else "ERROR"
                )

            # 2. Comprobar Tailscale Funnel :8443 apuntando a local_port cada 120 segundos
            if now - last_funnel_check >= 120.0:
                last_funnel_check = now
                funnel_ok = is_funnel_8443_pointing_to(local_port)
                if not funnel_ok and srv_ok:
                    restore_funnel_8443(local_port)
                    time.sleep(1.0)
                    funnel_ok = is_funnel_8443_pointing_to(local_port)
                    if funnel_ok:
                        recovery_msg = f"Túnel Tailscale Funnel (:8443 -> :{local_port}) restaurado automáticamente"
                        append_audit_record(
                            "WATCHDOG_FUNNEL_RESTORED",
                            {"funnel_port": FUNNEL_PORT, "local_port": local_port},
                            status_str="SUCCESS"
                        )

            update_health_file(
                local_port=local_port,
                server_ok=srv_ok,
                funnel_ok=funnel_ok,
                restart_timestamps=restart_timestamps,
                circuit_breaker=False,
                port_conflict=False,
                recovery_event=recovery_msg
            )
        except Exception:
            pass

        time.sleep(30)


if __name__ == "__main__":
    if "--install-startup" in sys.argv:
        custom_dir = None
        if "--target-dir" in sys.argv:
            idx = sys.argv.index("--target-dir")
            if idx + 1 < len(sys.argv):
                custom_dir = Path(sys.argv[idx + 1]).resolve()
        info = register_resilient_autostart(install_root=custom_dir)
        print(json.dumps(info, ensure_ascii=False))
        sys.exit(0)
    run_watchdog_loop()
