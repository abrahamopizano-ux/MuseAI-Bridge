r"""
MUSE AI PC BRIDGE — DESINSTALADOR OFICIAL (v2.1.0)
Integrado con "Agregar o quitar programas" de Windows (HKCU\...\Uninstall\MuseBridge):
- Elimina la Tarea Programada (MuseAIBridgeWatchdog), la clave HKCU Run y el VBS de inicio si corresponde.
- Elimina los accesos directos del Escritorio, Menú Inicio y Barra de Tareas.
- Pregunta al usuario si desea conservar la configuración (permissions.json y .env).
- Cero ventanas negras de consola (CREATE_NO_WINDOW = 0x08000000).
"""

import os
import sys
import shutil
import subprocess
import winreg
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

TASK_NAME = "MuseAIBridgeWatchdog"
UNINSTALL_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MuseBridge"
RUN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _resolve_install_dir() -> Path:
    for i, arg in enumerate(sys.argv):
        if arg == "--install-dir" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).resolve()
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name.lower() == "dist":
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parent


INSTALL_DIR = _resolve_install_dir()


def run_hidden(cmd, timeout=8):
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


def perform_uninstall(target_dir: Path, keep_config: bool = True, remove_system_autostart: bool = True) -> dict:
    removed_items = []

    # 1. Eliminar Tarea Programada y clave Run si se solicita
    if remove_system_autostart:
        run_hidden(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_SET_VALUE) as rk:
                winreg.DeleteValue(rk, TASK_NAME)
                removed_items.append("Registry HKCU Run")
        except OSError:
            pass

        startup_vbs = Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\iniciar-puente.vbs"))
        if startup_vbs.exists():
            try:
                content = startup_vbs.read_text(encoding="utf-8", errors="ignore")
                if str(target_dir).lower() in content.lower():
                    startup_vbs.unlink()
                    removed_items.append(str(startup_vbs))
            except Exception:
                pass

    # 2. Eliminar accesos directos (Escritorio, OneDrive Escritorio, Menú Inicio y Barra de Tareas)
    shortcut_locations = [
        Path(os.path.expandvars(r"%USERPROFILE%\Desktop\Muse AI Bridge.lnk")),
        Path(os.path.expandvars(r"%USERPROFILE%\OneDrive\Escritorio\Muse AI Bridge.lnk")),
        Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Muse AI Bridge.lnk")),
        Path(os.path.expandvars(r"%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Muse AI Bridge.lnk")),
    ]
    for lnk in shortcut_locations:
        if lnk.exists():
            try:
                lnk.unlink()
                removed_items.append(str(lnk))
            except Exception:
                pass

    # 3. Eliminar entrada de Agregar o quitar programas
    if remove_system_autostart:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY)
            removed_items.append("Add/Remove Programs Entry")
        except OSError:
            pass

    # 4. Eliminar archivos de la carpeta de instalación (conservando permissions.json y .env si keep_config=True)
    preserved_files = {"permissions.json", ".env"} if keep_config else set()
    if target_dir.exists() and target_dir.is_dir():
        for child in list(target_dir.iterdir()):
            if child.name in preserved_files:
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed_items.append(child.name)
            except Exception:
                pass

        if not keep_config:
            try:
                shutil.rmtree(target_dir, ignore_errors=True)
            except Exception:
                pass

    return {
        "confirmed": True,
        "target_dir": str(target_dir),
        "keep_config": keep_config,
        "removed_items": removed_items,
    }


class UninstallerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Desinstalar MuseBridge v2.1.0")
        self.root.geometry("540x340")
        self.root.resizable(False, False)

        self.c_bg = "#F7F4EF"
        self.c_surface = "#FFFFFF"
        self.c_navy = "#0D1F3C"
        self.c_red = "#C0392B"
        self.c_text = "#1A1A18"
        self.c_text2 = "#5A5A54"
        self.c_border = "#E2DDD4"

        self.root.configure(bg=self.c_bg)
        ico = INSTALL_DIR / "musebridge.ico"
        if ico.exists():
            try:
                self.root.iconbitmap(default=str(ico))
            except Exception:
                pass

        self.build_ui()

    def build_ui(self):
        hdr = tk.Frame(self.root, bg=self.c_navy, padx=24, pady=18)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Desinstalador de MuseBridge v2.1.0", font=("Georgia", 16), bg=self.c_navy, fg="#FFFFFF").pack(anchor="w")
        tk.Label(hdr, text=f"Ubicación: {INSTALL_DIR}", font=("Consolas", 8), bg=self.c_navy, fg="#93A5C2").pack(anchor="w", pady=(3, 0))

        card = tk.Frame(self.root, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=22, pady=18)
        card.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(
            card,
            text="Se eliminarán del equipo los archivos de MuseBridge, los accesos directos\n"
                 "del Escritorio y Barra de Tareas, la Tarea Programada y el registro de auto-inicio.",
            font=("Segoe UI", 10),
            bg=self.c_surface,
            fg=self.c_text,
            justify="left"
        ).pack(anchor="w", pady=(0, 14))

        self.var_keep_config = tk.BooleanVar(value=True)
        tk.Checkbutton(
            card,
            text="Conservar mi configuración y lista de permisos (permissions.json y .env)",
            variable=self.var_keep_config,
            bg=self.c_surface,
            fg=self.c_text,
            selectcolor=self.c_surface,
            activebackground=self.c_surface,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2"
        ).pack(anchor="w", pady=6)

        footer = tk.Frame(self.root, bg=self.c_bg, padx=20, pady=10)
        footer.pack(fill="x", side="bottom")

        tk.Button(
            footer,
            text="Cancelar",
            command=self.root.destroy,
            bg="#E2DDD4",
            fg=self.c_text,
            font=("Segoe UI", 10),
            relief="flat",
            padx=16,
            pady=7,
            cursor="hand2"
        ).pack(side="right", padx=(8, 0))

        tk.Button(
            footer,
            text="🗑️  Desinstalar Ahora",
            command=self.run_uninstall,
            bg=self.c_red,
            fg="#FFFFFF",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=18,
            pady=7,
            cursor="hand2"
        ).pack(side="right")

    def run_uninstall(self):
        keep = bool(self.var_keep_config.get())
        if not messagebox.askyesno(
            "Confirmar Desinstalación",
            f"¿Estás seguro de que deseas desinstalar MuseBridge de:\n{INSTALL_DIR}?\n\n"
            f"Conservar permissions.json: {'Sí' if keep else 'No (Borrar todo)'}"
        ):
            return
        perform_uninstall(INSTALL_DIR, keep_config=keep, remove_system_autostart=True)
        messagebox.showinfo(
            "Desinstalación Completada",
            "MuseBridge fue desinstalado correctamente.\n"
            + ("Se conservó tu archivo permissions.json." if keep else "Se eliminaron todos los archivos.")
        )
        self.root.destroy()


def main():
    if "--silent" in sys.argv:
        keep = "--delete-config" not in sys.argv
        remove_hooks = "--keep-system-hooks" not in sys.argv
        res = perform_uninstall(INSTALL_DIR, keep_config=keep, remove_system_autostart=remove_hooks)
        print(res)
        sys.exit(0)

    root = tk.Tk()
    UninstallerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
