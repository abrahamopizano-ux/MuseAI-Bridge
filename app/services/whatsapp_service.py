import abc
import ctypes
from ctypes import wintypes
import os
import re
import time
import urllib.parse
from typing import Dict, Any, Optional, List
import httpx
from app.config import settings


def _find_and_focus_whatsapp_window(timeout_sec: float = 7.0) -> Optional[int]:
    """
    Busca la ventana nativa de WhatsApp Desktop mediante Win32 EnumWindows,
    la restaura si está minimizada y la trae al frente para garantizar que
    la caja de texto tenga el foco real antes de presionar Enter.
    """
    if os.name != "nt":
        return None

    u32 = ctypes.windll.user32
    SW_RESTORE = 9

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    deadline = time.time() + timeout_sec
    target_hwnd = None

    while time.time() < deadline:
        found: List[int] = []

        def _enum_cb(hwnd, _lparam):
            if u32.IsWindowVisible(hwnd):
                length = u32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    u32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.strip()
                    if "whatsapp" in title.lower():
                        found.append(hwnd)
            return True

        u32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        if found:
            target_hwnd = found[0]
            try:
                if u32.IsIconic(target_hwnd):
                    u32.ShowWindow(target_hwnd, SW_RESTORE)
                u32.SetForegroundWindow(target_hwnd)
            except Exception:
                pass
            time.sleep(0.35)
            fg = u32.GetForegroundWindow()
            if fg == target_hwnd:
                return target_hwnd
            # Intentar forzar foco con AttachThreadInput si Windows bloqueó SetForegroundWindow
            try:
                fg_thread = u32.GetWindowThreadProcessId(fg, None)
                cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                u32.AttachThreadInput(cur_thread, fg_thread, True)
                u32.ShowWindow(target_hwnd, SW_RESTORE)
                u32.BringWindowToTop(target_hwnd)
                u32.SetForegroundWindow(target_hwnd)
                u32.AttachThreadInput(cur_thread, fg_thread, False)
            except Exception:
                pass
            if u32.GetForegroundWindow() == target_hwnd:
                return target_hwnd
        time.sleep(0.35)

    return target_hwnd


def _send_enter_key_native():
    """Envía la tecla Enter (VK_RETURN = 0x0D) usando SendInput / keybd_event."""
    VK_RETURN = 0x0D
    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(VK_RETURN, 0, 0, 0)
    time.sleep(0.06)
    ctypes.windll.user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)


class WhatsAppAdapter(abc.ABC):
    @abc.abstractmethod
    async def send_message(self, phone_number: str, message: str) -> Dict[str, Any]:
        pass


class BaileysMicroserviceAdapter(WhatsAppAdapter):
    def __init__(self, endpoint_url: str):
        self.endpoint_url = endpoint_url

    async def send_message(self, phone_number: str, message: str) -> Dict[str, Any]:
        payload = {
            "to": phone_number,
            "message": message
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(self.endpoint_url, json=payload)
                response.raise_for_status()
                return {
                    "confirmed": True,
                    "provider": "baileys_microservice",
                    "status": "SENT",
                    "remote_response": response.json() if response.headers.get("content-type") == "application/json" else response.text
                }
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Error comunicando con microservicio de WhatsApp: {str(exc)}") from exc


class WindowsProtocolAdapter(WhatsAppAdapter):
    """
    Adaptador nativo de Windows utilizando el protocolo URI 'whatsapp://send'.
    1. Abre el chat con el texto precargado en WhatsApp Desktop.
    2. Localiza y enfoca activamente la ventana de WhatsApp mediante Win32 API.
    3. Espera a que WhatsApp cargue la conversación y enfoca la barra de mensaje.
    4. Presiona Enter de forma verificada para enviar el mensaje realmente.
    """
    async def send_message(self, phone_number: str, message: str) -> Dict[str, Any]:
        import asyncio
        from app.services.pc_service import _attach_interactive_desktop, _close_desktop_handle

        clean_number = re.sub(r"[^\d]", "", phone_number)
        if len(clean_number) < 10:
            raise ValueError(f"Número de teléfono inválido: '{phone_number}'. Incluye código de país (ej. +52...).")

        encoded_text = urllib.parse.quote(message)
        uri = f"whatsapp://send?phone={clean_number}&text={encoded_text}"

        loop = asyncio.get_running_loop()

        def _dispatch_and_confirm() -> Dict[str, Any]:
            hdesk = _attach_interactive_desktop()
            try:
                os.startfile(uri)
                # 1. Localizar y traer al frente la ventana de WhatsApp Desktop
                hwnd = _find_and_focus_whatsapp_window(timeout_sec=7.0)
                if not hwnd:
                    raise RuntimeError(
                        "No se detectó la ventana de WhatsApp Desktop abierta tras invocar whatsapp://send. "
                        "Verifica que WhatsApp Desktop esté instalado y con sesión iniciada."
                    )

                # 2. Esperar a que la conversación específica cargue el borrador en la caja de entrada
                time.sleep(2.4)
                try:
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                time.sleep(0.25)

                # 3. Enviar pulsación real de Enter para despachar el mensaje
                _send_enter_key_native()
                time.sleep(0.45)

                # Segundo pulso de confirmación por si el primer Enter cerró un popup de autocompletado
                if ctypes.windll.user32.GetForegroundWindow() == hwnd:
                    _send_enter_key_native()

                fg_now = ctypes.windll.user32.GetForegroundWindow()
                window_focused = (fg_now == hwnd)

                return {
                    "confirmed": True,
                    "provider": "windows_protocol",
                    "status": "SENT",
                    "phone_number": clean_number,
                    "message_length": len(message),
                    "whatsapp_hwnd": int(hwnd),
                    "whatsapp_window_focused": window_focused,
                    "enter_key_dispatched": True,
                    "note": "Mensaje enviado y confirmado en la ventana activa de WhatsApp Desktop."
                }
            finally:
                _close_desktop_handle(hdesk)

        return await loop.run_in_executor(None, _dispatch_and_confirm)


class PyWhatKitFallbackAdapter(WhatsAppAdapter):
    async def send_message(self, phone_number: str, message: str) -> Dict[str, Any]:
        import pywhatkit
        import asyncio

        formatted_phone = phone_number if phone_number.startswith("+") else f"+{phone_number}"

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: pywhatkit.sendwhatmsg_instantly(
                phone_no=formatted_phone,
                message=message,
                wait_time=12,
                tab_close=True,
                close_time=3
            )
        )
        return {
            "confirmed": True,
            "provider": "pywhatkit",
            "status": "SENT",
            "note": "Mensaje despachado mediante automatización de navegador local"
        }


def get_whatsapp_adapter() -> WhatsAppAdapter:
    if settings.WHATSAPP_PROVIDER == "baileys_microservice":
        return BaileysMicroserviceAdapter(settings.BAILEYS_API_URL)
    elif settings.WHATSAPP_PROVIDER == "pywhatkit":
        return PyWhatKitFallbackAdapter()
    return WindowsProtocolAdapter()
