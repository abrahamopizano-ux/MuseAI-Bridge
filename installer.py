"""
MUSE AI PC BRIDGE — ASISTENTE DE INSTALACIÓN (WIZARD v2.1.0)
Asistente de instalación multipaso en español al estilo estándar de Windows:
- Paso 1 (Bienvenida): Presentación de MuseBridge v2.1.0, detección automática de Tailscale
  (con instrucciones claras si no está instalado, sin bloquear la instalación) y botón Siguiente.
- Paso 2 (Accesos directos): Dos casillas seleccionables por el usuario:
  [x] Crear icono en el escritorio
  [x] Anclar a la barra de tareas
  (Ambos abren el Panel / Dashboard de MuseBridge con su icono .ico, jamás una consola).
- Paso 3 (Ubicación): Carpeta por defecto (%LOCALAPPDATA%\\MuseAI-Bridge) y botón Examinar.
- Paso 4 (Instalación): Barra de progreso y bitácora mientras instala el servidor, el panel,
  el icono musebridge.ico, permissions.json con todos los permisos en false por defecto,
  el auto-inicio silencioso con el watchdog (Tarea Programada MuseAIBridgeWatchdog cada 5 min
  + HKCU Run + iniciar-puente.vbs) y el registro en "Agregar o quitar programas" con desinstalador.
- Paso 5 (Pantalla final): Resumen completo, casilla [x] Abrir el panel de MuseBridge y botón Finalizar.
- Cero ventanas negras de consola en todo el proceso (CREATE_NO_WINDOW = 0x08000000).
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import winreg
from pathlib import Path
from typing import Dict, Any
import tkinter as tk
from tkinter import ttk, messagebox, filedialog


def _resolve_payload_root() -> Path:
    """
    Localiza los archivos fuente a instalar:
    1. Si corre desde la carpeta del repositorio o dist/, usa la raíz del proyecto.
    2. Si corre como .exe autónomo en otra carpeta, usa los datos empaquetados en sys._MEIPASS.
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "app").exists() and (exe_dir / "desktop_app.py").exists():
            return exe_dir
        if exe_dir.name.lower() == "dist" and (exe_dir.parent / "app").exists():
            return exe_dir.parent
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and (Path(meipass) / "app").exists():
            return Path(meipass)
        return exe_dir
    return Path(__file__).resolve().parent


PAYLOAD_ROOT = _resolve_payload_root()
DEFAULT_INSTALL_DIR = Path(os.path.expandvars(r"%LOCALAPPDATA%\MuseAI-Bridge"))
STARTUP_DIR = Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"))
START_MENU_PROGRAMS = Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"))
TASKBAR_PIN_DIR = Path(os.path.expandvars(r"%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"))

TASK_NAME = "MuseAIBridgeWatchdog"
UNINSTALL_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MuseBridge"
RUN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
BRIDGE_VERSION = "2.1.0"


def run_hidden(cmd, timeout=15):
    kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000
    return subprocess.run(cmd, **kwargs)


def popen_hidden(cmd, cwd=None):
    kwargs = {
        "cwd": str(cwd) if cwd else None,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000
    return subprocess.Popen(cmd, **kwargs)


def detect_desktop_dir() -> Path:
    for candidate in (
        Path(os.path.expandvars(r"%USERPROFILE%\Desktop")),
        Path(os.path.expandvars(r"%USERPROFILE%\OneDrive\Escritorio")),
        Path(os.path.expandvars(r"%USERPROFILE%\OneDrive\Desktop")),
    ):
        if candidate.exists():
            return candidate
    return Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))


def check_tailscale_installed() -> Dict[str, Any]:
    """Comprueba si Tailscale está instalado y operativo sin interrumpir la instalación."""
    ts_path = shutil.which("tailscale")
    if not ts_path:
        for candidate in (
            Path(r"C:\Program Files\Tailscale\tailscale.exe"),
            Path(r"C:\Program Files (x86)\Tailscale\tailscale.exe"),
        ):
            if candidate.exists():
                ts_path = str(candidate)
                break

    if not ts_path:
        return {
            "installed": False,
            "path": None,
            "message": (
                "⚠️ Tailscale no fue detectado en este PC.\n"
                "La instalación continuará normalmente. Para exponer el puente a Muse AI:\n"
                "1. Descarga e instala Tailscale desde https://tailscale.com/download\n"
                "2. Inicia sesión en tu cuenta de Tailscale.\n"
                "3. Activa el botón 'Funnel (:8443)' desde el panel de MuseBridge."
            )
        }

    return {
        "installed": True,
        "path": ts_path,
        "message": f"✔ Tailscale detectado correctamente ({ts_path}).\nEl túnel seguro en el puerto 8443 está listo para vincularse con Muse AI."
    }


def build_default_deny_permissions(existing_catalog: dict = None) -> dict:
    """
    Construye permissions.json con TODOS los permisos en false por defecto,
    tal como exige la política de seguridad Default-Deny.
    """
    catalog = existing_catalog or {}
    if not catalog:
        try:
            if str(PAYLOAD_ROOT) not in sys.path:
                sys.path.insert(0, str(PAYLOAD_ROOT))
            from app.services.app_scanner import scan_installed_apps
            catalog = scan_installed_apps()
        except Exception:
            catalog = {}

    allowed_apps = {app_id: False for app_id in catalog}
    return {
        "kill_switch_active": False,
        "ui_theme": "claro",
        "allow_screenshot": False,
        "allow_whatsapp": False,
        "allow_gui_control": False,
        "allow_open_app": False,
        "allow_file_list": False,
        "allow_file_read": False,
        "allow_file_write": False,
        "allow_file_mkdir": False,
        "allow_delete": False,
        "allow_file_move": False,
        "allow_file_search": False,
        "allow_file_transfer": False,
        "allowed_paths": [],
        "allowed_apps": allowed_apps,
        "installed_apps_catalog": catalog,
    }


def create_windows_shortcut(lnk_path: Path, target_exe: str, arguments: str, working_dir: Path, icon_path: Path, description: str) -> bool:
    """Crea un archivo .lnk nativo usando VBScript oculto (0 consolas negras)."""
    try:
        lnk_path.parent.mkdir(parents=True, exist_ok=True)
        vbs_code = (
            'Set oWS = WScript.CreateObject("WScript.Shell")\r\n'
            f'Set oLink = oWS.CreateShortcut("{lnk_path}")\r\n'
            f'oLink.TargetPath = "{target_exe}"\r\n'
        )
        if arguments:
            vbs_code += f'oLink.Arguments = "{arguments}"\r\n'
        vbs_code += (
            f'oLink.WorkingDirectory = "{working_dir}"\r\n'
            f'oLink.Description = "{description}"\r\n'
        )
        if icon_path.exists():
            vbs_code += f'oLink.IconLocation = "{icon_path},0"\r\n'
        vbs_code += 'oLink.Save\r\n'

        temp_vbs = working_dir / "_create_lnk_tmp.vbs"
        with open(temp_vbs, "w", encoding="utf-8") as f:
            f.write(vbs_code)
        run_hidden(["cscript", "//nologo", str(temp_vbs)], timeout=8)
        temp_vbs.unlink(missing_ok=True)
        return lnk_path.exists()
    except Exception:
        return False


def pin_shortcut_to_taskbar(target_dir: Path, panel_target_exe: str, panel_args: str, icon_path: Path) -> Dict[str, Any]:
    """
    Ancla el acceso directo del Panel de MuseBridge a la Barra de Tareas y al Menú Inicio:
    1. Crea el acceso directo en la carpeta User Pinned\\TaskBar de Windows.
    2. Crea el acceso directo en el Menú Inicio (Programs\\Muse AI Bridge.lnk).
    3. Invoca el verbo Shell 'taskbarpin' si está disponible en la versión de Windows.
    """
    TASKBAR_PIN_DIR.mkdir(parents=True, exist_ok=True)
    taskbar_lnk = TASKBAR_PIN_DIR / "Muse AI Bridge.lnk"
    startmenu_lnk = START_MENU_PROGRAMS / "Muse AI Bridge.lnk"

    ok_tb = create_windows_shortcut(
        lnk_path=taskbar_lnk,
        target_exe=panel_target_exe,
        arguments=panel_args,
        working_dir=target_dir,
        icon_path=icon_path,
        description="MuseBridge v2.1.0 — Centro de Control (Panel)"
    )
    ok_sm = create_windows_shortcut(
        lnk_path=startmenu_lnk,
        target_exe=panel_target_exe,
        arguments=panel_args,
        working_dir=target_dir,
        icon_path=icon_path,
        description="MuseBridge v2.1.0 — Centro de Control (Panel)"
    )

    # Intentar invocar verbo de anclado en Windows Shell sin ventana
    ps_pin = (
        f'$shell = New-Object -ComObject Shell.Application; '
        f'$folder = $shell.Namespace("{startmenu_lnk.parent}"); '
        f'$item = $folder.ParseName("{startmenu_lnk.name}"); '
        f'if ($item) {{ $item.Verbs() | Where-Object {{ $_.Name -match "bar|tarea|taskbar" }} | ForEach-Object {{ $_.DoIt() }} }}'
    )
    run_hidden(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps_pin], timeout=6)

    return {
        "taskbar_lnk": str(taskbar_lnk),
        "startmenu_lnk": str(startmenu_lnk),
        "created": ok_tb or ok_sm
    }


def register_add_remove_programs(target_dir: Path, icon_path: Path, uninstaller_cmd: str) -> bool:
    """Registra MuseBridge v2.1.0 en 'Agregar o quitar programas' (HKCU\\...\\Uninstall\\MuseBridge)."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY) as uk:
            winreg.SetValueEx(uk, "DisplayName", 0, winreg.REG_SZ, "MuseBridge — Centro de Control PC & WhatsApp")
            winreg.SetValueEx(uk, "DisplayVersion", 0, winreg.REG_SZ, BRIDGE_VERSION)
            winreg.SetValueEx(uk, "Publisher", 0, winreg.REG_SZ, "Muse AI · PC Agent Bridge")
            winreg.SetValueEx(uk, "InstallLocation", 0, winreg.REG_SZ, str(target_dir))
            winreg.SetValueEx(uk, "DisplayIcon", 0, winreg.REG_SZ, str(icon_path))
            winreg.SetValueEx(uk, "UninstallString", 0, winreg.REG_SZ, uninstaller_cmd)
            winreg.SetValueEx(uk, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(uk, "NoRepair", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(uk, "EstimatedSize", 0, winreg.REG_DWORD, 46080)
        return True
    except Exception:
        return False


def is_reparse_point_or_junction(p: Path) -> bool:
    """Detecta si una carpeta es un enlace simbólico o JUNCTION de Windows (FILE_ATTRIBUTE_REPARSE_POINT = 0x400)."""
    if not p.exists() and not p.is_symlink():
        return False
    if p.is_symlink():
        return True
    if hasattr(os.path, "isjunction") and os.path.isjunction(p):
        return True
    try:
        attrs = os.lstat(p).st_file_attributes
        return bool(attrs & 0x0400)
    except Exception:
        return False


def find_system_python_executable() -> str:
    """Localiza el intérprete Python real del sistema para crear entornos virtuales reales (py -m venv)."""
    py_launcher = shutil.which("py")
    if py_launcher:
        return py_launcher

    candidates = [
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe")),
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe")),
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe")),
        Path(r"C:\Program Files\Python313\python.exe"),
        Path(r"C:\Program Files\Python312\python.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    which_py = shutil.which("python")
    if which_py:
        return which_py

    if not getattr(sys, "frozen", False):
        return sys.executable
    raise RuntimeError("No se encontró una instalación base de Python (py.exe / python.exe) en el sistema.")


def execute_full_installation(
    target_dir: Path,
    create_desktop_icon: bool = True,
    pin_to_taskbar: bool = True,
    register_system_hooks: bool = True,
    progress_cb=None
) -> Dict[str, Any]:
    """
    Ejecuta la instalación completa paso a paso:
    1. Copia el código del servidor, panel, run_server.py, watchdog, desinstalador e icono (.ico).
    2. PRESERVA permissions.json y .env si ya existen en la carpeta destino; solo los crea si no existen.
    3. Crea un entorno virtual REAL (py -m venv venv + pip install -r requirements.txt), NUNCA un junction ni enlace.
    4. Registra todos los launchers (.bat, .vbs, Tarea Programada y HKCU Run) apuntando a target_dir y SIN --reload.
    """
    def _report(msg: str, pct: int):
        if progress_cb:
            progress_cb(msg, pct)

    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "dist").mkdir(parents=True, exist_ok=True)

    # 1. Copiar código del servidor, panel, run_server.py, watchdog, desinstalador e icono (.ico)
    _report("Copiando código del servidor (app/), run_server.py, panel y servicio vigilante...", 15)
    items_to_copy = [
        "app",
        "requirements.txt",
        "run_server.py",
        "desktop_app.py",
        "watchdog_service.py",
        "uninstaller.py",
        "installer.py",
        "README.md",
        "musebridge.ico",
    ]
    for item in items_to_copy:
        src = PAYLOAD_ROOT / item
        dst = target_dir / item
        if src.exists() and src.resolve() != dst.resolve():
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(src, dst)

    # 2. PRESERVAR .env: solo crearlo si no existe en target_dir
    dst_env_file = target_dir / ".env"
    env_preserved = dst_env_file.exists()
    if not env_preserved:
        src_env = PAYLOAD_ROOT / ".env"
        if src_env.exists():
            shutil.copy2(src_env, dst_env_file)
        else:
            new_tok = f"sk_live_muse_bridge_{secrets.token_hex(16)}"
            dst_env_file.write_text(
                f"API_SECRET_TOKEN={new_tok}\n"
                "SERVER_HOST=127.0.0.1\n"
                "SERVER_PORT=8000\n"
                "DEBUG=False\n"
                "SCREENSHOT_MAX_WIDTH=1920\n"
                "SCREENSHOT_QUALITY=85\n"
                "WHATSAPP_PROVIDER=windows_protocol\n"
                "BAILEYS_API_URL=http://127.0.0.1:3000/send-message\n",
                encoding="utf-8"
            )

    # Copiar ejecutables precompilados si existen (MuseAI-Bridge.exe y Uninstall-MuseBridge.exe)
    _report("Copiando ejecutables del Centro de Control y Desinstalador...", 30)
    for exe_name in ("MuseAI-Bridge.exe", "Uninstall-MuseBridge.exe"):
        for candidate in (
            PAYLOAD_ROOT / "dist" / exe_name,
            PAYLOAD_ROOT / exe_name,
        ):
            if candidate.exists():
                dst_exe = target_dir / "dist" / exe_name
                if candidate.resolve() != dst_exe.resolve():
                    shutil.copy2(candidate, dst_exe)
                root_exe = target_dir / exe_name
                if candidate.resolve() != root_exe.resolve():
                    shutil.copy2(candidate, root_exe)
                break

    # 3. PRESERVAR permissions.json si ya existe; solo crearlo con todo en false si NO existe
    _report("Verificando permissions.json (preservando configuración existente o creando por defecto)...", 45)
    dst_perms_file = target_dir / "permissions.json"
    permissions_preserved = dst_perms_file.exists()
    if not permissions_preserved:
        existing_catalog = {}
        src_perms = PAYLOAD_ROOT / "permissions.json"
        if src_perms.exists():
            try:
                with open(src_perms, "r", encoding="utf-8") as f:
                    existing_catalog = json.load(f).get("installed_apps_catalog", {})
            except Exception:
                existing_catalog = {}
        default_perms = build_default_deny_permissions(existing_catalog=existing_catalog)
        with open(dst_perms_file, "w", encoding="utf-8") as f:
            json.dump(default_perms, f, indent=2, ensure_ascii=False)

    # 4. Crear entorno virtual REAL (py -m venv venv + pip install -r requirements.txt), NUNCA un junction
    _report("Creando entorno virtual Python REAL (py -m venv + pip install)...", 60)
    dst_venv = target_dir / "venv"
    if is_reparse_point_or_junction(dst_venv):
        try:
            os.rmdir(dst_venv)
        except Exception:
            shutil.rmtree(dst_venv, ignore_errors=True)

    venv_python = dst_venv / "Scripts" / "python.exe"
    venv_pip = dst_venv / "Scripts" / "pip.exe"
    req_file = target_dir / "requirements.txt"

    if not venv_python.exists():
        base_py = find_system_python_executable()
        res_venv = run_hidden([base_py, "-m", "venv", str(dst_venv)], timeout=90)
        if res_venv.returncode != 0 or not venv_python.exists():
            raise RuntimeError(f"Error creando entorno virtual real en {dst_venv}: {res_venv.stderr}")

    _report("Instalando dependencias en el entorno virtual real (pip install -r requirements.txt)...", 72)
    res_pip = run_hidden(
        [str(venv_python), "-m", "pip", "install", "-r", str(req_file), "--disable-pip-version-check", "--quiet"],
        timeout=300
    )
    if res_pip.returncode != 0:
        raise RuntimeError(f"Error instalando dependencias en {dst_venv}: {res_pip.stderr}")

    pythonw_exe = dst_venv / "Scripts" / "pythonw.exe"
    if not pythonw_exe.exists():
        pythonw_exe = venv_python

    # Determinar el ejecutable que abre el PANEL (Dashboard)
    panel_exe_file = target_dir / "MuseAI-Bridge.exe"
    if not panel_exe_file.exists():
        panel_exe_file = target_dir / "dist" / "MuseAI-Bridge.exe"

    if panel_exe_file.exists():
        panel_target_exe = str(panel_exe_file)
        panel_args = ""
    else:
        panel_target_exe = str(pythonw_exe)
        panel_args = f'"{target_dir / "desktop_app.py"}"'

    icon_path = target_dir / "musebridge.ico"

    # 5. Configurar auto-inicio oculto con el Watchdog (apuntando a target_dir y SIN --reload)
    _report("Registrando auto-inicio oculto con el Watchdog (sin --reload, rutas de instalación actual)...", 82)
    watchdog_script = target_dir / "watchdog_service.py"
    run_server_script = target_dir / "run_server.py"
    bat_path = target_dir / "iniciar-puente.bat"
    bat_content = (
        '@echo off\r\n'
        f'cd /d "{target_dir}"\r\n'
        f'if exist "{pythonw_exe}" (\r\n'
        f'    start "" "{pythonw_exe}" "{watchdog_script}"\r\n'
        ') else (\r\n'
        f'    "{venv_python}" "{run_server_script}" --host 127.0.0.1 --port 8000\r\n'
        ')\r\n'
    )
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    scheduled_task_ok = False
    reg_run_ok = False
    startup_vbs_path = STARTUP_DIR / "iniciar-puente.vbs"

    if register_system_hooks:
        if STARTUP_DIR.exists():
            vbs_content = (
                'Set WshShell = CreateObject("WScript.Shell")\r\n'
                f'WshShell.CurrentDirectory = "{target_dir}"\r\n'
                f'WshShell.Run """{pythonw_exe}"" ""{watchdog_script}""", 0, False\r\n'
            )
            with open(startup_vbs_path, "w", encoding="utf-8") as f:
                f.write(vbs_content)

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_SET_VALUE) as rk:
                winreg.SetValueEx(rk, TASK_NAME, 0, winreg.REG_SZ, f'"{pythonw_exe}" "{watchdog_script}"')
            reg_run_ok = True
        except Exception:
            pass

        task_cmd = f'"{pythonw_exe}" "{watchdog_script}"'
        res_sch = run_hidden(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", task_cmd, "/SC", "MINUTE", "/MO", "5", "/F"],
            timeout=10
        )
        scheduled_task_ok = (res_sch.returncode == 0)

    # 6. Crear los accesos directos elegidos
    _report("Creando accesos directos seleccionados por el usuario...", 90)
    desktop_lnk_created = False
    desktop_lnk_path = detect_desktop_dir() / "Muse AI Bridge.lnk"
    if create_desktop_icon:
        desktop_lnk_created = create_windows_shortcut(
            lnk_path=desktop_lnk_path,
            target_exe=panel_target_exe,
            arguments=panel_args,
            working_dir=target_dir,
            icon_path=icon_path,
            description="MuseBridge v2.1.0 — Centro de Control (Panel)"
        )

    taskbar_info = {"created": False, "taskbar_lnk": None}
    if pin_to_taskbar:
        taskbar_info = pin_shortcut_to_taskbar(
            target_dir=target_dir,
            panel_target_exe=panel_target_exe,
            panel_args=panel_args,
            icon_path=icon_path
        )

    # 7. Registrar en "Agregar o quitar programas" con desinstalador
    _report("Registrando desinstalador en Agregar o quitar programas de Windows...", 96)
    uninst_exe = target_dir / "Uninstall-MuseBridge.exe"
    if uninst_exe.exists():
        uninst_cmd = f'"{uninst_exe}" --install-dir "{target_dir}"'
    else:
        uninst_cmd = f'"{pythonw_exe}" "{target_dir / "uninstaller.py"}" --install-dir "{target_dir}"'

    add_remove_ok = register_add_remove_programs(target_dir, icon_path, uninst_cmd) if register_system_hooks else False

    _report("¡Instalación finalizada con éxito!", 100)
    return {
        "confirmed": True,
        "install_dir": str(target_dir),
        "real_venv_created": venv_python.exists() and not is_reparse_point_or_junction(dst_venv),
        "venv_is_junction": is_reparse_point_or_junction(dst_venv),
        "permissions_preserved": permissions_preserved,
        "env_preserved": env_preserved,
        "panel_target_exe": panel_target_exe,
        "panel_args": panel_args,
        "icon_path": str(icon_path),
        "permissions_file": str(dst_perms_file),
        "desktop_shortcut_created": desktop_lnk_created,
        "desktop_shortcut_path": str(desktop_lnk_path) if desktop_lnk_created else None,
        "taskbar_pinned": taskbar_info["created"],
        "taskbar_shortcut_path": taskbar_info.get("taskbar_lnk"),
        "scheduled_task_registered": scheduled_task_ok,
        "registry_run_registered": reg_run_ok,
        "startup_vbs_path": str(startup_vbs_path),
        "add_remove_programs_registered": add_remove_ok,
        "uninstaller_command": uninst_cmd,
    }


class WizardInstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"Asistente de Instalación — MuseBridge v{BRIDGE_VERSION}")
        self.root.geometry("780x540")
        self.root.resizable(False, False)

        # Paleta Moderna MuseBridge (Estilo Oscuro Carbon a juego con el panel)
        self.c_bg = "#0B0D11"
        self.c_surface = "#131720"
        self.c_surface2 = "#1B202C"
        self.c_surface2_hover = "#242B3A"
        self.c_sidebar = "#0E1117"
        self.c_navy = "#2563EB"
        self.c_navy_hover = "#3B82F6"
        self.c_navy_light = "#142242"
        self.c_green = "#10B981"
        self.c_green_hover = "#059669"
        self.c_green_light = "#0E2922"
        self.c_amber = "#F59E0B"
        self.c_amber_light = "#2C2110"
        self.c_text = "#F3F4F6"
        self.c_text2 = "#9CA3AF"
        self.c_text3 = "#6B7280"
        self.c_border = "#222734"
        self.c_border_hover = "#384156"

        self.root.configure(bg=self.c_bg)

        for ico_candidate in (
            PAYLOAD_ROOT / "musebridge.ico",
            Path(getattr(sys, "_MEIPASS", str(PAYLOAD_ROOT))) / "musebridge.ico",
        ):
            if ico_candidate.exists():
                try:
                    self.root.iconbitmap(default=str(ico_candidate))
                    break
                except Exception:
                    pass

        # Variables de estado del asistente
        self.current_step = 1
        self.var_shortcut_desktop = tk.BooleanVar(value=True)
        self.var_shortcut_taskbar = tk.BooleanVar(value=True)
        self.var_install_dir = tk.StringVar(value=str(DEFAULT_INSTALL_DIR))
        self.var_open_panel = tk.BooleanVar(value=True)
        self.tailscale_info = check_tailscale_installed()
        self.install_result: Dict[str, Any] = {}
        self.is_installing = False

        self.step_labels: Dict[int, tk.Label] = {}

        self.setup_styles()
        self.build_shell()
        self.show_step(1)

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Wizard.Horizontal.TProgressbar",
            background=self.c_navy,
            troughcolor=self.c_surface2,
            bordercolor=self.c_border,
            borderwidth=0,
            thickness=14
        )

    def build_shell(self):
        main_wrap = tk.Frame(self.root, bg=self.c_bg)
        main_wrap.pack(fill="both", expand=True)

        # Barra lateral izquierda con los 5 pasos del asistente
        self.sidebar = tk.Frame(main_wrap, bg=self.c_sidebar, width=220)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Divisor vertical entre barra lateral y área de contenido
        tk.Frame(main_wrap, bg=self.c_border, width=1).pack(side="left", fill="y")

        brand = tk.Frame(self.sidebar, bg=self.c_sidebar, padx=20, pady=24)
        brand.pack(fill="x")
        tk.Label(brand, text="MuseBridge", font=("Segoe UI Semibold", 18), bg=self.c_sidebar, fg="#FFFFFF").pack(anchor="w")
        tk.Label(brand, text=f"INSTALADOR · v{BRIDGE_VERSION}", font=("Segoe UI Semibold", 8), bg=self.c_sidebar, fg=self.c_text3).pack(anchor="w", pady=(3, 0))

        tk.Frame(self.sidebar, bg=self.c_border, height=1).pack(fill="x", pady=(0, 14))

        steps_box = tk.Frame(self.sidebar, bg=self.c_sidebar, padx=14)
        steps_box.pack(fill="x")

        step_titles = [
            (1, "1. Bienvenida"),
            (2, "2. Accesos directos"),
            (3, "3. Ubicación"),
            (4, "4. Instalación"),
            (5, "5. Finalizar"),
        ]
        for s_num, s_txt in step_titles:
            lbl = tk.Label(
                steps_box,
                text=f"  {s_txt}",
                font=("Segoe UI", 10),
                bg=self.c_sidebar,
                fg=self.c_text2,
                anchor="w",
                padx=10,
                pady=9
            )
            lbl.pack(fill="x", pady=2)
            self.step_labels[s_num] = lbl

        footer_side = tk.Frame(self.sidebar, bg=self.c_sidebar, padx=20, pady=16)
        footer_side.pack(side="bottom", fill="x")
        tk.Label(
            footer_side,
            text="Muse AI · PC Bridge\nAsistente de Windows",
            font=("Segoe UI", 8),
            bg=self.c_sidebar,
            fg=self.c_text3,
            justify="left"
        ).pack(anchor="w")

        # Área derecha (Contenido del paso + Barra inferior de botones)
        right_area = tk.Frame(main_wrap, bg=self.c_bg)
        right_area.pack(side="left", fill="both", expand=True)

        self.page_container = tk.Frame(right_area, bg=self.c_bg, padx=28, pady=24)
        self.page_container.pack(fill="both", expand=True)

        tk.Frame(right_area, bg=self.c_border, height=1).pack(fill="x")
        self.bottom_bar = tk.Frame(right_area, bg=self.c_surface, padx=24, pady=14)
        self.bottom_bar.pack(fill="x", side="bottom")

        self.btn_cancel = tk.Button(
            self.bottom_bar,
            text="Cancelar",
            command=self.on_cancel,
            bg=self.c_surface2,
            fg=self.c_text,
            activebackground=self.c_surface2_hover,
            activeforeground=self.c_text,
            font=("Segoe UI Semibold", 9),
            relief="flat",
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2"
        )
        self.btn_cancel.pack(side="left")

        self.btn_next = tk.Button(
            self.bottom_bar,
            text="Siguiente  >",
            command=self.go_next,
            bg=self.c_navy,
            fg="#FFFFFF",
            activebackground=self.c_navy_hover,
            activeforeground="#FFFFFF",
            font=("Segoe UI Semibold", 9),
            relief="flat",
            bd=0,
            padx=22,
            pady=7,
            cursor="hand2"
        )
        self.btn_next.pack(side="right")

        self.btn_back = tk.Button(
            self.bottom_bar,
            text="<  Atrás",
            command=self.go_back,
            bg=self.c_surface2,
            fg=self.c_text,
            activebackground=self.c_surface2_hover,
            activeforeground=self.c_text,
            font=("Segoe UI Semibold", 9),
            relief="flat",
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2"
        )
        self.btn_back.pack(side="right", padx=(0, 10))

    def update_sidebar_highlight(self):
        step_names = {
            1: "Bienvenida",
            2: "Accesos directos",
            3: "Ubicación",
            4: "Instalación",
            5: "Finalizar",
        }
        for s_num, lbl in self.step_labels.items():
            if s_num < self.current_step:
                lbl.config(
                    text=f"  ✔  {s_num}. {step_names[s_num]}",
                    bg=self.c_sidebar,
                    fg=self.c_green,
                    font=("Segoe UI Semibold", 10)
                )
            elif s_num == self.current_step:
                lbl.config(
                    text=f"  ●  {s_num}. {step_names[s_num]}",
                    bg=self.c_surface,
                    fg="#FFFFFF",
                    font=("Segoe UI Semibold", 10)
                )
            else:
                lbl.config(
                    text=f"     {s_num}. {step_names[s_num]}",
                    bg=self.c_sidebar,
                    fg=self.c_text3,
                    font=("Segoe UI", 10)
                )

    def show_step(self, step_num: int):
        self.current_step = step_num
        self.update_sidebar_highlight()

        for child in self.page_container.winfo_children():
            child.destroy()

        if step_num == 1:
            self.build_step_1_welcome()
        elif step_num == 2:
            self.build_step_2_shortcuts()
        elif step_num == 3:
            self.build_step_3_location()
        elif step_num == 4:
            self.build_step_4_installing()
        elif step_num == 5:
            self.build_step_5_finish()

    # -------------------------------------------------------------------------
    # PASO 1: PANTALLA DE BIENVENIDA + VERIFICACIÓN DE TAILSCALE
    # -------------------------------------------------------------------------
    def build_step_1_welcome(self):
        self.btn_back.config(state="disabled")
        self.btn_next.config(state="normal", text="Siguiente  >")
        self.btn_cancel.config(state="normal")

        tk.Label(
            self.page_container,
            text="Bienvenido al Asistente de Instalación de MuseBridge",
            font=("Segoe UI Semibold", 15),
            bg=self.c_bg,
            fg=self.c_text
        ).pack(anchor="w", pady=(0, 4))

        tk.Label(
            self.page_container,
            text=f"Este asistente instalará MuseBridge v{BRIDGE_VERSION} (Servidor Puente y Centro de Control) en tu equipo.",
            font=("Segoe UI", 9),
            bg=self.c_bg,
            fg=self.c_text2
        ).pack(anchor="w", pady=(0, 12))

        card = tk.Frame(self.page_container, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=20, pady=16)
        card.pack(fill="both", expand=True)

        tk.Label(
            card,
            text="¿Qué incluye esta instalación?",
            font=("Segoe UI Semibold", 11),
            bg=self.c_surface,
            fg=self.c_text
        ).pack(anchor="w", pady=(0, 6))

        bullets = (
            "• Panel de Control de Escritorio responsive con Modo Claro y Oscuro, QR y auditoría.\n"
            "• Servidor Puente FastAPI con captura nativa 1920x1080, tipeo Unicode y sandbox de archivos.\n"
            "• Servicio Vigilante (Watchdog) silencioso que auto-recupera el puente automáticamente.\n"
            "• Modelo de seguridad Denegado por Defecto (permissions.json con todo en false).\n"
            "• Desinstalador oficial integrado en 'Agregar o quitar programas' de Windows."
        )
        tk.Label(card, text=bullets, font=("Segoe UI", 9), bg=self.c_surface, fg=self.c_text2, justify="left").pack(anchor="w", pady=(0, 12))

        # Cuadro de verificación de Tailscale con botón directo de descarga
        ts_ok = self.tailscale_info["installed"]
        ts_bg = self.c_green_light if ts_ok else self.c_amber_light
        ts_border = self.c_green if ts_ok else self.c_amber
        ts_fg = "#10B981" if ts_ok else "#F59E0B"
        ts_box = tk.Frame(card, bg=ts_bg, highlightbackground=ts_border, highlightthickness=1, padx=14, pady=10)
        ts_box.pack(fill="x", pady=(2, 0))

        ts_header_row = tk.Frame(ts_box, bg=ts_bg)
        ts_header_row.pack(fill="x")

        tk.Label(
            ts_header_row,
            text="Comprobación de Red Tailscale:",
            font=("Segoe UI Semibold", 9),
            bg=ts_bg,
            fg=ts_fg
        ).pack(side="left")

        # Botón para descargar Tailscale
        btn_dl_tailscale = tk.Button(
            ts_header_row,
            text="🌐  Descargar Tailscale Gratis",
            command=lambda: webbrowser.open("https://tailscale.com/download/windows"),
            bg=self.c_navy,
            fg="#FFFFFF",
            activebackground=self.c_navy_hover,
            activeforeground="#FFFFFF",
            font=("Segoe UI Semibold", 8),
            relief="flat",
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2"
        )
        btn_dl_tailscale.pack(side="right")

        tk.Label(
            ts_box,
            text=self.tailscale_info["message"],
            font=("Segoe UI", 8),
            bg=ts_bg,
            fg=self.c_text,
            justify="left",
            wraplength=460
        ).pack(anchor="w", pady=(4, 0))

        tk.Label(
            self.page_container,
            text="Haz clic en 'Siguiente >' para continuar.",
            font=("Segoe UI", 8),
            bg=self.c_bg,
            fg=self.c_text3
        ).pack(anchor="w", pady=(8, 0))

    # -------------------------------------------------------------------------
    # PASO 2: PÁGINA DE ACCESOS DIRECTOS
    # -------------------------------------------------------------------------
    def build_step_2_shortcuts(self):
        self.btn_back.config(state="normal")
        self.btn_next.config(state="normal", text="Siguiente  >")

        tk.Label(
            self.page_container,
            text="Configuración de Accesos Directos",
            font=("Segoe UI Semibold", 15),
            bg=self.c_bg,
            fg=self.c_text
        ).pack(anchor="w", pady=(0, 4))

        tk.Label(
            self.page_container,
            text="Elige los accesos directos que deseas crear para abrir el Panel de Control de MuseBridge.",
            font=("Segoe UI", 9),
            bg=self.c_bg,
            fg=self.c_text2
        ).pack(anchor="w", pady=(0, 12))

        card = tk.Frame(self.page_container, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=22, pady=18)
        card.pack(fill="both", expand=True)

        cb1_box = tk.Frame(card, bg=self.c_surface2, highlightbackground=self.c_border, highlightthickness=1, padx=14, pady=12)
        cb1_box.pack(fill="x", pady=(0, 10))
        tk.Checkbutton(
            cb1_box,
            text="🖥️   Crear icono en el escritorio",
            variable=self.var_shortcut_desktop,
            bg=self.c_surface2,
            fg=self.c_text,
            selectcolor=self.c_surface,
            activebackground=self.c_surface2,
            activeforeground=self.c_text,
            font=("Segoe UI Semibold", 10),
            cursor="hand2"
        ).pack(anchor="w")
        tk.Label(
            cb1_box,
            text="Crea el acceso directo 'Muse AI Bridge' en tu Escritorio con icono oficial (.ico) para abrir el Panel Visual.",
            font=("Segoe UI", 8),
            bg=self.c_surface2,
            fg=self.c_text2
        ).pack(anchor="w", padx=(26, 0))

        cb2_box = tk.Frame(card, bg=self.c_surface2, highlightbackground=self.c_border, highlightthickness=1, padx=14, pady=12)
        cb2_box.pack(fill="x", pady=(0, 12))
        tk.Checkbutton(
            cb2_box,
            text="📌   Anclar a la barra de tareas y Menú Inicio",
            variable=self.var_shortcut_taskbar,
            bg=self.c_surface2,
            fg=self.c_text,
            selectcolor=self.c_surface,
            activebackground=self.c_surface2,
            activeforeground=self.c_text,
            font=("Segoe UI Semibold", 10),
            cursor="hand2"
        ).pack(anchor="w")
        tk.Label(
            cb2_box,
            text="Agrega el acceso directo de MuseBridge en la barra de tareas y en el Menú Inicio de Windows.",
            font=("Segoe UI", 8),
            bg=self.c_surface2,
            fg=self.c_text2
        ).pack(anchor="w", padx=(26, 0))

        note = tk.Frame(card, bg=self.c_navy_light, highlightbackground=self.c_navy, highlightthickness=1, padx=12, pady=10)
        note.pack(fill="x")
        tk.Label(
            note,
            text="ℹ️  Los accesos directos abren directamente el Panel de Control con diseño moderno,\n"
                 "    garantizando cero ventanas negras de consola.",
            font=("Segoe UI Semibold", 8),
            bg=self.c_navy_light,
            fg="#60A5FA",
            justify="left"
        ).pack(anchor="w")

    # -------------------------------------------------------------------------
    # PASO 3: PÁGINA DE UBICACIÓN
    # -------------------------------------------------------------------------
    def build_step_3_location(self):
        self.btn_back.config(state="normal")
        self.btn_next.config(state="normal", text="Instalar  >")

        tk.Label(
            self.page_container,
            text="Carpeta de Instalación",
            font=("Segoe UI Semibold", 15),
            bg=self.c_bg,
            fg=self.c_text
        ).pack(anchor="w", pady=(0, 4))

        tk.Label(
            self.page_container,
            text="Selecciona la carpeta donde se instalará MuseBridge v2.1.0.",
            font=("Segoe UI", 9),
            bg=self.c_bg,
            fg=self.c_text2
        ).pack(anchor="w", pady=(0, 12))

        card = tk.Frame(self.page_container, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=22, pady=18)
        card.pack(fill="both", expand=True)

        tk.Label(
            card,
            text="Carpeta de destino (por defecto %LOCALAPPDATA%\\MuseAI-Bridge):",
            font=("Segoe UI Semibold", 9),
            bg=self.c_surface,
            fg=self.c_text
        ).pack(anchor="w", pady=(0, 8))

        path_row = tk.Frame(card, bg=self.c_surface)
        path_row.pack(fill="x", pady=(0, 14))

        self.entry_dir = tk.Entry(
            path_row,
            textvariable=self.var_install_dir,
            font=("Consolas", 9),
            bg=self.c_surface2,
            fg=self.c_text,
            insertbackground=self.c_text,
            relief="flat",
            highlightbackground=self.c_border,
            highlightthickness=1
        )
        self.entry_dir.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 10))

        tk.Button(
            path_row,
            text="📂  Examinar...",
            command=self.browse_install_folder,
            bg=self.c_navy,
            fg="#FFFFFF",
            activebackground=self.c_navy_hover,
            activeforeground="#FFFFFF",
            font=("Segoe UI Semibold", 9),
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2"
        ).pack(side="right")

        info_box = tk.Frame(card, bg=self.c_surface2, highlightbackground=self.c_border, highlightthickness=1, padx=14, pady=12)
        info_box.pack(fill="x")
        tk.Label(
            info_box,
            text="Resumen de elementos que se instalarán en esta ubicación:\n"
                 "• Servidor Puente FastAPI (app/) y Servicio Vigilante (watchdog_service.py)\n"
                 "• Panel de Control (MuseAI-Bridge.exe y desktop_app.py) con icono musebridge.ico\n"
                 "• Archivo permissions.json con todos los permisos desactivados (false) por defecto\n"
                 "• Desinstalador oficial (Uninstall-MuseBridge.exe) registrado en Windows",
            font=("Segoe UI", 8),
            bg=self.c_surface2,
            fg=self.c_text2,
            justify="left"
        ).pack(anchor="w")

    def browse_install_folder(self):
        chosen = filedialog.askdirectory(
            title="Seleccionar carpeta de instalación para MuseBridge",
            initialdir=str(Path(self.var_install_dir.get()).parent)
        )
        if chosen:
            self.var_install_dir.set(str(Path(chosen)))

    # -------------------------------------------------------------------------
    # PASO 4: PÁGINA DE INSTALACIÓN (PROGRESO EN TIEMPO REAL)
    # -------------------------------------------------------------------------
    def build_step_4_installing(self):
        self.btn_back.config(state="disabled")
        self.btn_next.config(state="disabled", text="Instalando...")
        self.btn_cancel.config(state="disabled")

        tk.Label(
            self.page_container,
            text="Instalando MuseBridge v2.1.0",
            font=("Segoe UI Semibold", 15),
            bg=self.c_bg,
            fg=self.c_text
        ).pack(anchor="w", pady=(0, 4))

        tk.Label(
            self.page_container,
            text="Por favor espera mientras se copian los archivos y se registra el servicio vigilante...",
            font=("Segoe UI", 9),
            bg=self.c_bg,
            fg=self.c_text2
        ).pack(anchor="w", pady=(0, 12))

        card = tk.Frame(self.page_container, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=22, pady=18)
        card.pack(fill="both", expand=True)

        self.lbl_prog_status = tk.Label(
            card,
            text="Iniciando instalación...",
            font=("Segoe UI Semibold", 9),
            bg=self.c_navy_light,
            fg="#60A5FA",
            padx=10,
            pady=4
        )
        self.lbl_prog_status.pack(anchor="w", pady=(0, 10))

        self.progress_bar = ttk.Progressbar(
            card,
            orient="horizontal",
            mode="determinate",
            length=480,
            style="Wizard.Horizontal.TProgressbar"
        )
        self.progress_bar.pack(fill="x", pady=(0, 12))

        self.install_log_list = tk.Listbox(
            card,
            font=("Consolas", 8),
            bg=self.c_surface2,
            fg=self.c_text,
            relief="flat",
            highlightbackground=self.c_border,
            highlightthickness=1,
            selectbackground=self.c_navy,
            selectforeground="#FFFFFF"
        )
        self.install_log_list.pack(fill="both", expand=True)

        self.is_installing = True
        threading.Thread(target=self._run_install_thread, daemon=True).start()

    def _on_progress_update(self, msg: str, pct: int):
        def _ui():
            self.lbl_prog_status.config(text=f"[{pct}%] {msg}")
            self.progress_bar.config(value=pct)
            self.install_log_list.insert(tk.END, f"✔  {msg}")
            self.install_log_list.see(tk.END)
        self.root.after(0, _ui)

    def _run_install_thread(self):
        try:
            target = Path(os.path.expandvars(self.var_install_dir.get().strip()))
            res = execute_full_installation(
                target_dir=target,
                create_desktop_icon=bool(self.var_shortcut_desktop.get()),
                pin_to_taskbar=bool(self.var_shortcut_taskbar.get()),
                register_system_hooks=True,
                progress_cb=self._on_progress_update
            )
            self.install_result = res
            self.is_installing = False
            time.sleep(0.5)
            self.root.after(0, lambda: self.show_step(5))
        except Exception as exc:
            self.is_installing = False
            self.root.after(0, lambda: messagebox.showerror("Error de Instalación", f"Ocurrió un error durante la instalación:\n{exc}"))
            self.root.after(0, lambda: self.show_step(3))

    # -------------------------------------------------------------------------
    # PASO 5: PANTALLA FINAL (RESUMEN Y CASILLA "ABRIR EL PANEL DE MUSEBRIDGE")
    # -------------------------------------------------------------------------
    def build_step_5_finish(self):
        self.btn_back.config(state="disabled")
        self.btn_cancel.config(state="disabled")
        self.btn_next.config(state="normal", text="Finalizar", bg=self.c_green, activebackground=self.c_green_hover)

        tk.Label(
            self.page_container,
            text="¡Instalación de MuseBridge Completada!",
            font=("Segoe UI Semibold", 15),
            bg=self.c_bg,
            fg=self.c_green
        ).pack(anchor="w", pady=(0, 4))

        tk.Label(
            self.page_container,
            text="MuseBridge v2.1.0 quedó instalado y configurado correctamente en tu equipo.",
            font=("Segoe UI", 9),
            bg=self.c_bg,
            fg=self.c_text2
        ).pack(anchor="w", pady=(0, 12))

        card = tk.Frame(self.page_container, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=22, pady=18)
        card.pack(fill="both", expand=True)

        res = self.install_result
        summary_lines = (
            f"✔  Carpeta instalada: {res.get('install_dir')}\n"
            f"✔  Permisos por defecto: permissions.json (Todos en false por seguridad)\n"
            f"✔  Icono en el Escritorio: {'Creado' if res.get('desktop_shortcut_created') else 'No seleccionado'}\n"
            f"✔  Anclado a Barra de Tareas: {'Creado' if res.get('taskbar_pinned') else 'No seleccionado'}\n"
            f"✔  Auto-inicio con Watchdog: Tarea '{TASK_NAME}' + HKCU Run + iniciar-puente.vbs\n"
            f"✔  Desinstalador Windows: Registrado en 'Agregar o quitar programas'"
        )
        tk.Label(
            card,
            text=summary_lines,
            font=("Segoe UI", 9),
            bg=self.c_surface,
            fg=self.c_text,
            justify="left"
        ).pack(anchor="w", pady=(0, 16))

        open_box = tk.Frame(card, bg=self.c_green_light, highlightbackground=self.c_green, highlightthickness=1, padx=14, pady=12)
        open_box.pack(fill="x", pady=(4, 0))

        tk.Checkbutton(
            open_box,
            text="🚀   Abrir el panel de MuseBridge al finalizar",
            variable=self.var_open_panel,
            bg=self.c_green_light,
            fg=self.c_green,
            selectcolor=self.c_surface,
            activebackground=self.c_green_light,
            activeforeground=self.c_green,
            font=("Segoe UI Semibold", 10),
            cursor="hand2"
        ).pack(anchor="w")

    def go_next(self):
        if self.current_step in (1, 2, 3):
            if self.current_step == 3:
                raw_dir = self.var_install_dir.get().strip()
                if not raw_dir:
                    messagebox.showwarning("Ruta Inválida", "Por favor ingresa o selecciona una carpeta de instalación.")
                    return
            self.show_step(self.current_step + 1)
        elif self.current_step == 5:
            self.finish_wizard()

    def go_back(self):
        if self.current_step in (2, 3) and not self.is_installing:
            self.show_step(self.current_step - 1)

    def on_cancel(self):
        if self.is_installing:
            return
        if messagebox.askyesno("Salir del Instalador", "¿Deseas cancelar la instalación de MuseBridge?"):
            self.root.destroy()

    def finish_wizard(self):
        res = self.install_result
        target_dir = Path(res.get("install_dir", str(DEFAULT_INSTALL_DIR)))

        # Asegurar que el servicio vigilante esté activo en segundo plano (sin reiniciar el puente si ya corre)
        pythonw_exe = target_dir / "venv" / "Scripts" / "pythonw.exe"
        watchdog_py = target_dir / "watchdog_service.py"
        if pythonw_exe.exists() and watchdog_py.exists():
            popen_hidden([str(pythonw_exe), str(watchdog_py)], cwd=target_dir)

        if self.var_open_panel.get():
            panel_exe = target_dir / "MuseAI-Bridge.exe"
            if not panel_exe.exists():
                panel_exe = target_dir / "dist" / "MuseAI-Bridge.exe"
            if panel_exe.exists():
                popen_hidden([str(panel_exe)], cwd=target_dir)
            elif pythonw_exe.exists() and (target_dir / "desktop_app.py").exists():
                popen_hidden([str(pythonw_exe), str(target_dir / "desktop_app.py")], cwd=target_dir)

        self.root.destroy()


def main():
    # Modo de prueba automatizada sin bloqueo para verificar instalación de principio a fin
    if "--auto-install-dir" in sys.argv:
        idx = sys.argv.index("--auto-install-dir")
        target = Path(sys.argv[idx + 1]).resolve()
        reg_hooks = "--preserve-live-autostart" not in sys.argv
        res = execute_full_installation(
            target_dir=target,
            create_desktop_icon=True,
            pin_to_taskbar=True,
            register_system_hooks=reg_hooks,
        )
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(0)

    root = tk.Tk()
    WizardInstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
