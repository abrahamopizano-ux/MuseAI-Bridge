import base64
import ctypes
from ctypes import wintypes
import io
import os
import platform
import subprocess
import time
from typing import Dict, Any, List, Optional, Tuple
import psutil
import pyautogui
from PIL import Image, ImageGrab

from app.config import settings
from app.schemas.pc import GuiAction, GuiActionType

BRIDGE_START_TIME = time.time()

# Configuración Win32 para captura de pantalla, portapapeles Unicode y SendInput
if platform.system().lower() == "windows":
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32

    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p

    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = wintypes.BOOL

    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = ctypes.c_void_p

    user32.SetThreadDesktop.argtypes = [ctypes.c_void_p]
    user32.SetThreadDesktop.restype = wintypes.BOOL

    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    user32.CloseDesktop.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p

    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p

    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p

    gdi32.BitBlt.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD
    ]
    gdi32.BitBlt.restype = wintypes.BOOL

    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = wintypes.BOOL

    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = wintypes.BOOL


# Estructuras Win32 SendInput para tipeo Unicode nativo (soporta acentos, ñ, símbolos)
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_V = 0x56


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else wintypes.ULONG),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else wintypes.ULONG),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTunion),
    ]


def _attach_interactive_desktop():
    if platform.system().lower() != "windows":
        return None
    try:
        hdesk = ctypes.windll.user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            ctypes.windll.user32.SetThreadDesktop(hdesk)
            return hdesk
    except Exception:
        pass
    return None


def _close_desktop_handle(hdesk):
    if hdesk and platform.system().lower() == "windows":
        try:
            ctypes.windll.user32.CloseDesktop(hdesk)
        except Exception:
            pass


def get_native_screen_resolution() -> Tuple[int, int]:
    if platform.system().lower() == "windows":
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            w = int(ctypes.windll.user32.GetSystemMetrics(0))
            h = int(ctypes.windll.user32.GetSystemMetrics(1))
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
    size = pyautogui.size()
    return int(size.width), int(size.height)


def _send_vk_key(vk_code: int, key_up: bool = False):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk_code
    inp.union.ki.wScan = 0
    inp.union.ki.dwFlags = KEYEVENTF_KEYUP if key_up else 0
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = 0
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _send_unicode_char(code_unit: int):
    down = INPUT()
    down.type = INPUT_KEYBOARD
    down.union.ki.wVk = 0
    down.union.ki.wScan = code_unit
    down.union.ki.dwFlags = KEYEVENTF_UNICODE
    down.union.ki.time = 0
    down.union.ki.dwExtraInfo = 0

    up = INPUT()
    up.type = INPUT_KEYBOARD
    up.union.ki.wVk = 0
    up.union.ki.wScan = code_unit
    up.union.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    up.union.ki.time = 0
    up.union.ki.dwExtraInfo = 0

    arr = (INPUT * 2)(down, up)
    ctypes.windll.user32.SendInput(2, ctypes.byref(arr), ctypes.sizeof(INPUT))


def _set_windows_clipboard_unicode(text: str) -> bool:
    """Copia texto UTF-16 completo al portapapeles de Windows preservando acentos y saltos de línea."""
    if platform.system().lower() != "windows":
        return False

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32

    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.restype = wintypes.BOOL
    u32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p

    # Normalizar saltos de línea para Windows (\r\n)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    encoded = normalized.encode("utf-16-le") + b"\x00\x00"

    hdesk = _attach_interactive_desktop()
    try:
        opened = False
        for _ in range(5):
            if u32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.03)
        if not opened:
            return False

        try:
            u32.EmptyClipboard()
            h_mem = k32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
            if not h_mem:
                return False
            ptr = k32.GlobalLock(h_mem)
            if not ptr:
                return False
            ctypes.memmove(ptr, encoded, len(encoded))
            k32.GlobalUnlock(h_mem)
            if not u32.SetClipboardData(CF_UNICODETEXT, h_mem):
                return False
            return True
        finally:
            u32.CloseClipboard()
    finally:
        _close_desktop_handle(hdesk)


def _paste_via_ctrl_v():
    _send_vk_key(VK_CONTROL, key_up=False)
    time.sleep(0.02)
    _send_vk_key(VK_V, key_up=False)
    time.sleep(0.02)
    _send_vk_key(VK_V, key_up=True)
    time.sleep(0.02)
    _send_vk_key(VK_CONTROL, key_up=True)
    time.sleep(0.08)


def _type_unicode_text(text: str, use_clipboard_for_long: bool = True) -> str:
    """
    Escribe cualquier texto con soporte completo de español (á, é, í, ó, ú, ñ, ¿, ¡),
    saltos de línea y textos largos sin límite de 900 caracteres.
    - Si el texto es largo (> 180 caracteres) o contiene saltos de línea y use_clipboard_for_long=True,
      lo inyecta de golpe vía portapapeles Unicode + Ctrl+V (y fallback a KEYEVENTF_UNICODE).
    - Para textos cortos o cuando se desactiva el portapapeles, envía cada unidad UTF-16 con KEYEVENTF_UNICODE.
    """
    if not text:
        return "empty"

    hdesk = _attach_interactive_desktop()
    try:
        if use_clipboard_for_long and (len(text) > 180 or "\n" in text):
            if _set_windows_clipboard_unicode(text):
                _paste_via_ctrl_v()
                return "clipboard_unicode_paste"

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        for ch in normalized:
            if ch == "\n":
                # Shift+Enter para salto de línea seguro sin enviar formularios por accidente
                _send_vk_key(VK_SHIFT, key_up=False)
                _send_vk_key(VK_RETURN, key_up=False)
                _send_vk_key(VK_RETURN, key_up=True)
                _send_vk_key(VK_SHIFT, key_up=True)
                time.sleep(0.01)
            else:
                utf16_units = ch.encode("utf-16-le")
                for i in range(0, len(utf16_units), 2):
                    unit = int.from_bytes(utf16_units[i:i + 2], "little")
                    _send_unicode_char(unit)
                time.sleep(0.004)
        return "sendinput_unicode"
    finally:
        _close_desktop_handle(hdesk)


def _grab_screen_native() -> Image.Image:
    """
    Captura la pantalla en su resolución real (ej. 1920x1080) incluso cuando el
    proceso corre en segundo plano o sin ventana de consola.
    """
    if platform.system().lower() != "windows":
        return ImageGrab.grab()

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    hdesk = _attach_interactive_desktop()
    try:
        return ImageGrab.grab(all_screens=False)
    except Exception:
        pass
    finally:
        _close_desktop_handle(hdesk)

    # Fallback directo a GDI BitBlt
    u32 = ctypes.windll.user32
    g32 = ctypes.windll.gdi32
    k32 = ctypes.windll.kernel32

    hdc_screen = u32.GetDC(None)
    if not hdc_screen:
        raise RuntimeError("No se pudo obtener el contexto de pantalla (GetDC)")

    hdc_mem = None
    hbmp = None
    old_obj = None
    try:
        w = u32.GetSystemMetrics(0)
        h = u32.GetSystemMetrics(1)
        hdc_mem = g32.CreateCompatibleDC(hdc_screen)
        hbmp = g32.CreateCompatibleBitmap(hdc_screen, w, h)
        old_obj = g32.SelectObject(hdc_mem, hbmp)

        res = g32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, 0, 0, 0x00CC0020)
        if not res:
            raise RuntimeError(f"GDI BitBlt falló con código {k32.GetLastError()}")

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buffer = ctypes.create_string_buffer(w * h * 4)
        g32.GetDIBits(hdc_mem, hbmp, 0, h, buffer, ctypes.byref(bmi), 0)

        img = Image.frombuffer("RGBA", (w, h), buffer, "raw", "BGRA", 0, 1)
        return img.convert("RGB")
    finally:
        if hdc_mem and old_obj:
            g32.SelectObject(hdc_mem, old_obj)
        if hbmp:
            g32.DeleteObject(hbmp)
        if hdc_mem:
            g32.DeleteDC(hdc_mem)
        u32.ReleaseDC(None, hdc_screen)


pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.03


class PCService:

    @staticmethod
    def open_allowed_application(app_alias: str) -> Dict[str, Any]:
        from app.core.permissions import resolve_authorized_app

        app_info = resolve_authorized_app(app_alias)
        app_id = app_info.get("id", app_alias.strip().lower())
        app_name = app_info.get("name", app_id)
        target_path = app_info.get("path", "")

        if not target_path:
            raise ValueError(f"La aplicación '{app_name}' no tiene una ruta ejecutable válida.")

        pid = None
        launched = False
        if target_path.lower().endswith(".lnk") or target_path.endswith(":"):
            os.startfile(target_path)
            launched = True
        else:
            try:
                process = subprocess.Popen(
                    [target_path] if isinstance(target_path, str) else target_path,
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x08000000 if os.name == "nt" else 0
                )
                pid = process.pid
                launched = True
            except Exception:
                os.startfile(target_path)
                launched = True

        time.sleep(0.3)
        process_alive = True
        if pid is not None:
            process_alive = psutil.pid_exists(pid)

        if not launched:
            raise RuntimeError(f"No se pudo confirmar el arranque de la aplicación '{app_name}'.")

        return {
            "confirmed": True,
            "app_id": app_id,
            "app_name": app_name,
            "pid": pid,
            "process_verified": process_alive,
            "status": "LAUNCHED"
        }

    @staticmethod
    def _scale_coords(
        x: Optional[int],
        y: Optional[int],
        source_width: Optional[int],
        source_height: Optional[int],
        native_w: int,
        native_h: int
    ) -> Tuple[Optional[int], Optional[int]]:
        if x is None or y is None:
            return None, None
        if source_width and source_height and (source_width != native_w or source_height != native_h):
            scaled_x = int(round(x * (native_w / float(source_width))))
            scaled_y = int(round(y * (native_h / float(source_height))))
        else:
            scaled_x, scaled_y = int(x), int(y)
        # Asegurar dentro de los límites reales del monitor
        scaled_x = max(0, min(native_w - 1, scaled_x))
        scaled_y = max(0, min(native_h - 1, scaled_y))
        return scaled_x, scaled_y

    @staticmethod
    def execute_gui_actions(
        actions: List[GuiAction],
        source_width: Optional[int] = None,
        source_height: Optional[int] = None
    ) -> Dict[str, Any]:
        native_w, native_h = get_native_screen_resolution()
        executed_count = 0
        action_results: List[Dict[str, Any]] = []

        for act in actions:
            real_x, real_y = PCService._scale_coords(
                act.x, act.y, source_width, source_height, native_w, native_h
            )

            if act.action == GuiActionType.MOVE_TO:
                pyautogui.moveTo(real_x, real_y, duration=act.duration)
                action_results.append({"action": "move_to", "x": real_x, "y": real_y, "confirmed": True})

            elif act.action == GuiActionType.CLICK:
                if real_x is not None and real_y is not None:
                    pyautogui.click(real_x, real_y, duration=act.duration)
                else:
                    pyautogui.click()
                action_results.append({"action": "click", "x": real_x, "y": real_y, "confirmed": True})

            elif act.action == GuiActionType.DOUBLE_CLICK:
                if real_x is not None and real_y is not None:
                    pyautogui.doubleClick(real_x, real_y)
                else:
                    pyautogui.doubleClick()
                action_results.append({"action": "double_click", "x": real_x, "y": real_y, "confirmed": True})

            elif act.action == GuiActionType.RIGHT_CLICK:
                if real_x is not None and real_y is not None:
                    pyautogui.rightClick(real_x, real_y)
                else:
                    pyautogui.rightClick()
                action_results.append({"action": "right_click", "x": real_x, "y": real_y, "confirmed": True})

            elif act.action in {GuiActionType.TYPE_TEXT, GuiActionType.PASTE_TEXT}:
                if real_x is not None and real_y is not None:
                    pyautogui.click(real_x, real_y)
                    time.sleep(0.08)
                use_clip = (act.action == GuiActionType.PASTE_TEXT) or (len(act.text or "") > 180) or ("\n" in (act.text or ""))
                method = _type_unicode_text(act.text or "", use_clipboard_for_long=use_clip)
                if act.press_enter:
                    time.sleep(0.05)
                    _send_vk_key(VK_RETURN, key_up=False)
                    time.sleep(0.03)
                    _send_vk_key(VK_RETURN, key_up=True)
                action_results.append({
                    "action": act.action.value,
                    "characters_sent": len(act.text or ""),
                    "method": method,
                    "press_enter": act.press_enter,
                    "confirmed": True
                })

            elif act.action == GuiActionType.PRESS_KEY:
                key_low = (act.key or "").strip().lower()
                if key_low in ("enter", "return"):
                    _send_vk_key(VK_RETURN, key_up=False)
                    time.sleep(0.03)
                    _send_vk_key(VK_RETURN, key_up=True)
                else:
                    pyautogui.press(key_low)
                action_results.append({"action": "press_key", "key": key_low, "confirmed": True})

            elif act.action == GuiActionType.HOTKEY:
                pyautogui.hotkey(*[k.lower() for k in (act.keys or [])])
                action_results.append({"action": "hotkey", "keys": act.keys, "confirmed": True})

            elif act.action == GuiActionType.SLEEP:
                time.sleep(act.duration)
                action_results.append({"action": "sleep", "duration": act.duration, "confirmed": True})

            executed_count += 1

        cur_pos = pyautogui.position()
        return {
            "confirmed": True,
            "executed_actions": executed_count,
            "native_screen_resolution": {"width": native_w, "height": native_h},
            "final_cursor_position": {"x": int(cur_pos.x), "y": int(cur_pos.y)},
            "results": action_results,
            "status": "COMPLETED"
        }

    @staticmethod
    def type_long_text(
        text: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        press_enter: bool = False,
        use_clipboard: bool = True
    ) -> Dict[str, Any]:
        native_w, native_h = get_native_screen_resolution()
        if x is not None and y is not None:
            real_x = max(0, min(native_w - 1, int(x)))
            real_y = max(0, min(native_h - 1, int(y)))
            pyautogui.click(real_x, real_y)
            time.sleep(0.1)

        method = _type_unicode_text(text, use_clipboard_for_long=use_clipboard)
        if press_enter:
            time.sleep(0.06)
            _send_vk_key(VK_RETURN, key_up=False)
            time.sleep(0.04)
            _send_vk_key(VK_RETURN, key_up=True)

        return {
            "confirmed": True,
            "status": "TEXT_DELIVERED",
            "characters_sent": len(text),
            "lines_sent": text.count("\n") + 1,
            "method": method,
            "press_enter": press_enter
        }

    @staticmethod
    def capture_optimized_screenshot(
        native_resolution: bool = True,
        max_width: Optional[int] = None,
        quality: Optional[int] = None,
        region_x: Optional[int] = None,
        region_y: Optional[int] = None,
        region_w: Optional[int] = None,
        region_h: Optional[int] = None,
        zoom: float = 1.0
    ) -> Dict[str, Any]:
        screenshot = _grab_screen_native()
        orig_width, orig_height = screenshot.size

        offset_x = 0
        offset_y = 0

        # Si se solicita una región específica (captura por zona / lupa / zoom)
        if region_x is not None and region_y is not None and region_w is not None and region_h is not None:
            x1 = max(0, min(orig_width - 1, int(region_x)))
            y1 = max(0, min(orig_height - 1, int(region_y)))
            x2 = max(x1 + 1, min(orig_width, x1 + int(region_w)))
            y2 = max(y1 + 1, min(orig_height, y1 + int(region_h)))
            screenshot = screenshot.crop((x1, y1, x2, y2))
            offset_x = x1
            offset_y = y1
            if zoom and zoom > 1.0:
                z = min(float(zoom), 4.0)
                zw = int(round(screenshot.width * z))
                zh = int(round(screenshot.height * z))
                screenshot = screenshot.resize((zw, zh), Image.Resampling.LANCZOS)
        elif not native_resolution and max_width and orig_width > max_width:
            # Solo reduce si explícitamente se pide native_resolution=False y un max_width menor
            ratio = max_width / float(orig_width)
            new_height = int(float(orig_height) * float(ratio))
            screenshot = screenshot.resize((max_width, new_height), Image.Resampling.LANCZOS)

        if screenshot.mode in ("RGBA", "P"):
            screenshot = screenshot.convert("RGB")

        jpeg_quality = quality if (quality and 30 <= quality <= 100) else settings.SCREENSHOT_QUALITY
        buffered = io.BytesIO()
        screenshot.save(
            buffered,
            format="JPEG",
            quality=jpeg_quality,
            optimize=True
        )
        raw_jpeg = buffered.getvalue()
        if not raw_jpeg:
            raise RuntimeError("La captura de pantalla generó un buffer vacío.")

        base64_encoded = base64.b64encode(raw_jpeg).decode("utf-8")
        if region_x is not None and region_w is not None:
            scale_x = round(1.0 / float(zoom if zoom > 1.0 else 1.0), 4)
            scale_y = scale_x
        else:
            scale_x = round(orig_width / float(screenshot.width), 4)
            scale_y = round(orig_height / float(screenshot.height), 4)

        return {
            "confirmed": True,
            "format": "image/jpeg",
            "base64_data": base64_encoded,
            "width": screenshot.width,
            "height": screenshot.height,
            "native_width": orig_width,
            "native_height": orig_height,
            "scale_factor_x": scale_x,
            "scale_factor_y": scale_y,
            "region_offset_x": offset_x,
            "region_offset_y": offset_y,
            "zoom_applied": float(zoom if zoom and zoom > 1.0 else 1.0)
        }

    @staticmethod
    def get_system_metrics() -> Dict[str, Any]:
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot_time = psutil.boot_time()
        uptime = time.time() - boot_time
        bridge_uptime = time.time() - BRIDGE_START_TIME
        native_w, native_h = get_native_screen_resolution()

        return {
            "confirmed": True,
            "platform": f"{platform.system()} {platform.release()}",
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "cpu_count": psutil.cpu_count(logical=True) or 1,
            "memory_total_gb": round(vm.total / (1024 ** 3), 2),
            "memory_used_gb": round(vm.used / (1024 ** 3), 2),
            "memory_percent": vm.percent,
            "disk_percent": disk.percent,
            "uptime_seconds": round(uptime, 2),
            "bridge_uptime_seconds": round(bridge_uptime, 2),
            "native_screen_width": native_w,
            "native_screen_height": native_h
        }
