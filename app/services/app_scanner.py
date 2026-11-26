"""
Escáner de Aplicaciones Instaladas en Windows para Muse AI Bridge.
Descubre aplicaciones desde:
1. Menú Inicio (ProgramData y AppData Start Menu .lnk)
2. Registro de Windows (Uninstall y App Paths en HKLM / HKCU)
3. Carpetas de Programas (Program Files, Program Files (x86), LocalAppData\\Programs)
4. Utilidades base del sistema (Notepad, Calculadora, Explorador, Terminal, PowerShell, WhatsApp)

Todas las aplicaciones descubiertas se registran denegadas por defecto (False).
"""

import os
import re
import winreg
from pathlib import Path
from typing import Dict, Any

# Palabras clave de desinstaladores o archivos auxiliares que no son apps de usuario
IGNORE_KEYWORDS = (
    "uninstall", "desinstalar", "uninst", "setup", "installer", "instalador",
    "update", "updater", "crash", "helper", "redist", "vcredist", "dxsetup",
    "readme", "leeme", "manual", "help", "ayuda", "license", "licencia",
    "website", "sitio web", "url", "diagnostic", "reporter", "daemon",
    "service", "llvm-", "python 3.", "pyinstaller"
)


def normalize_app_id(name: str) -> str:
    """Convierte un nombre de aplicación en un identificador limpio y estable."""
    clean = name.strip().lower()
    clean = re.sub(r"[^a-z0-9áéíóúñ]+", "_", clean)
    clean = clean.strip("_")
    return clean[:64] if clean else "unknown_app"


def is_ignored_name(name: str) -> bool:
    low = name.lower().strip()
    if len(low) < 2:
        return True
    for kw in IGNORE_KEYWORDS:
        if kw in low:
            return True
    return False


def _scan_start_menu(catalog: Dict[str, Dict[str, Any]]) -> None:
    """Escanea accesos directos (.lnk) del Menú Inicio de todos los usuarios y del usuario actual."""
    start_dirs = [
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
        Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
        Path(os.environ.get("USERPROFILE", "")) / r"OneDrive\Escritorio",
    ]

    for base_dir in start_dirs:
        if not base_dir.exists():
            continue
        try:
            for lnk_path in base_dir.rglob("*.lnk"):
                stem = lnk_path.stem.strip()
                if is_ignored_name(stem):
                    continue
                app_id = normalize_app_id(stem)
                if app_id not in catalog:
                    catalog[app_id] = {
                        "id": app_id,
                        "name": stem,
                        "path": str(lnk_path.resolve()),
                        "source": "Menú Inicio"
                    }
        except Exception:
            pass


def _scan_registry_app_paths(catalog: Dict[str, Dict[str, Any]]) -> None:
    """Escanea ejecutables registrados en App Paths de Windows."""
    reg_roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]

    for hive, subkey_path in reg_roots:
        try:
            with winreg.OpenKey(hive, subkey_path) as base_key:
                num_subkeys, _, _ = winreg.QueryInfoKey(base_key)
                for i in range(num_subkeys):
                    try:
                        exe_name = winreg.EnumKey(base_key, i)
                        if not exe_name.lower().endswith(".exe"):
                            continue
                        with winreg.OpenKey(base_key, exe_name) as subkey:
                            val, _ = winreg.QueryValueEx(subkey, "")
                            if not val:
                                continue
                            clean_path = str(val).strip().strip('"')
                            p = Path(clean_path)
                            if p.exists() and p.is_file():
                                stem = p.stem
                                if is_ignored_name(stem):
                                    continue
                                display_name = stem.replace("_", " ").replace("-", " ").title()
                                app_id = normalize_app_id(stem)
                                if app_id not in catalog:
                                    catalog[app_id] = {
                                        "id": app_id,
                                        "name": display_name,
                                        "path": str(p),
                                        "source": "Registro"
                                    }
                    except Exception:
                        continue
        except Exception:
            pass


def _scan_registry_uninstall(catalog: Dict[str, Dict[str, Any]]) -> None:
    """Escanea programas instalados desde las claves Uninstall del Registro."""
    uninstall_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    for hive, subkey_path in uninstall_paths:
        try:
            with winreg.OpenKey(hive, subkey_path) as base_key:
                num_subkeys, _, _ = winreg.QueryInfoKey(base_key)
                for i in range(num_subkeys):
                    try:
                        sub_name = winreg.EnumKey(base_key, i)
                        with winreg.OpenKey(base_key, sub_name) as subkey:
                            try:
                                display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                            except OSError:
                                continue

                            if not display_name or is_ignored_name(str(display_name)):
                                continue

                            exe_candidate = None
                            # 1. Intentar obtener ejecutable desde DisplayIcon
                            try:
                                icon_val, _ = winreg.QueryValueEx(subkey, "DisplayIcon")
                                if icon_val:
                                    icon_path = str(icon_val).split(",")[0].strip().strip('"')
                                    if icon_path.lower().endswith(".exe") and Path(icon_path).exists():
                                        if not is_ignored_name(Path(icon_path).stem):
                                            exe_candidate = icon_path
                            except OSError:
                                pass

                            # 2. Intentar desde InstallLocation
                            if not exe_candidate:
                                try:
                                    loc_val, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                                    if loc_val:
                                        loc_dir = Path(str(loc_val).strip().strip('"'))
                                        if loc_dir.exists() and loc_dir.is_dir():
                                            for exe_f in loc_dir.glob("*.exe"):
                                                if not is_ignored_name(exe_f.stem):
                                                    exe_candidate = str(exe_f)
                                                    break
                                except OSError:
                                    pass

                            if exe_candidate:
                                app_id = normalize_app_id(str(display_name))
                                if app_id not in catalog:
                                    catalog[app_id] = {
                                        "id": app_id,
                                        "name": str(display_name).strip(),
                                        "path": exe_candidate,
                                        "source": "Registro"
                                    }
                    except Exception:
                        continue
        except Exception:
            pass


def _scan_program_directories(catalog: Dict[str, Dict[str, Any]]) -> None:
    """Escanea carpetas de primer nivel en Program Files y LocalAppData\\Programs."""
    prog_dirs = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    ]

    for pdir in prog_dirs:
        if not pdir.exists():
            continue
        try:
            for folder in pdir.iterdir():
                if not folder.is_dir():
                    continue
                try:
                    for exe_file in folder.glob("*.exe"):
                        stem = exe_file.stem
                        if is_ignored_name(stem):
                            continue
                        app_id = normalize_app_id(stem)
                        if app_id not in catalog:
                            catalog[app_id] = {
                                "id": app_id,
                                "name": f"{folder.name} ({stem})",
                                "path": str(exe_file),
                                "source": "Program Files"
                            }
                except Exception:
                    continue
        except Exception:
            pass


def _add_builtin_apps(catalog: Dict[str, Dict[str, Any]]) -> None:
    """Agrega herramientas base del sistema y accesos clave."""
    local_app_data = os.environ.get("LOCALAPPDATA", r"C:\Users\abrah\AppData\Local")
    antigravity_exe = Path(local_app_data) / "Programs" / "Antigravity" / "Antigravity.exe"

    builtins = [
        ("antigravity", "Antigravity IDE", str(antigravity_exe) if antigravity_exe.exists() else "antigravity", "Sistema"),
        ("whatsapp", "WhatsApp Desktop", "whatsapp:", "Sistema"),
        ("notepad", "Bloc de Notas (Notepad)", "notepad.exe", "Sistema"),
        ("calculator", "Calculadora de Windows", "calc.exe", "Sistema"),
        ("explorer", "Explorador de Archivos", "explorer.exe", "Sistema"),
        ("terminal", "Símbolo del Sistema (CMD)", "cmd.exe", "Sistema"),
        ("powershell", "Windows PowerShell", "powershell.exe", "Sistema"),
    ]

    for app_id, name, path, source in builtins:
        if app_id not in catalog:
            catalog[app_id] = {
                "id": app_id,
                "name": name,
                "path": path,
                "source": source
            }


def scan_installed_apps() -> Dict[str, Dict[str, Any]]:
    """
    Escanea el PC y devuelve un diccionario ordenado de aplicaciones instaladas:
    {
        app_id: {
            "id": str,
            "name": str,
            "path": str,
            "source": str
        }
    }
    """
    catalog: Dict[str, Dict[str, Any]] = {}
    _add_builtin_apps(catalog)
    _scan_start_menu(catalog)
    _scan_registry_app_paths(catalog)
    _scan_registry_uninstall(catalog)
    _scan_program_directories(catalog)

    # Ordenar alfabéticamente por nombre
    sorted_items = sorted(catalog.values(), key=lambda x: x["name"].lower())
    return {item["id"]: item for item in sorted_items}
