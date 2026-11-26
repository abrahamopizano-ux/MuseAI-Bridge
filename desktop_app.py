"""
MUSE AI // PC AGENT BRIDGE — CENTRO DE CONTROL DE ESCRITORIO (v2.1.0)
Diseño Responsive y Refinado (Inspirado en Linear, Vercel, Stripe y Configuración de Windows 11):
- Layout 100% adaptable a cualquier tamaño de ventana (1280x720, 1024x768, 800x600 y ventana pequeña):
  * Barra lateral auto-colapsable a riel de iconos (60px) en ventanas estrechas con tooltips flotantes.
  * Barra superior auto-envolvente (1 o 2 filas) para que el título y el botón rojo DETENER CONEXIÓN jamás se corten.
  * Cuadrícula de tarjetas KPI adaptable dinámicamente (4 columnas -> 2x2 -> 1 columna).
  * Contenedor FlowFrame que envuelve botones en varias filas automáticamente cuando el ancho se reduce.
  * Vistas divididas (QR, Puertos, Archivos) que pasan de 2 columnas a 1 columna apilada con scroll en ventana pequeña.
  * Tablas de Auditoría y Puertos con desplazamiento horizontal y vertical + tooltip con el texto completo al pasar el cursor.
- Estética sobria y pulida (Modo Oscuro Carbon y Modo Claro Slate), tipografía Segoe UI + Consolas consistente,
  micro-interacciones de hover/press en botones y tarjetas, y transición suave de entrada al cambiar de sección.
- Cero ventanas negras de consola (CREATE_NO_WINDOW = 0x08000000).
"""

import os
import sys
import json
import time
import socket
import secrets
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


def _resolve_project_root() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name.lower() == "dist" and (exe_dir.parent / "permissions.json").exists():
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parent


PROJECT_ROOT = _resolve_project_root()
VENV_PYTHONW = PROJECT_ROOT / "venv" / "Scripts" / "pythonw.exe"
VENV_PYTHON = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"

IS_FROZEN = getattr(sys, "frozen", False)
if not IS_FROZEN and VENV_PYTHONW.exists():
    try:
        curr_exe = Path(sys.executable).resolve()
        if curr_exe != VENV_PYTHONW.resolve() and curr_exe != VENV_PYTHON.resolve():
            subprocess.Popen(
                [str(VENV_PYTHONW), str(PROJECT_ROOT / "desktop_app.py")],
                cwd=str(PROJECT_ROOT),
                creationflags=0x08000000 if os.name == "nt" else 0
            )
            sys.exit(0)
    except Exception:
        pass

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import psutil
except ImportError:
    psutil = None

try:
    import qrcode
    from PIL import Image, ImageTk
    HAS_QR = True
except ImportError:
    HAS_QR = False

from app.core.permissions import (
    load_permissions,
    save_permissions,
    sync_scanned_apps,
    is_forbidden_system_path,
)
from app.core.port_utils import (
    get_configured_local_port,
    save_configured_local_port,
    inspect_port,
    scan_port_range,
    validate_port_selection,
)
from app.services.app_scanner import scan_installed_apps
from app.services.health_service import perform_health_check, load_health_state, BRIDGE_VERSION

ENV_FILE = PROJECT_ROOT / ".env"
LOG_FILE = PROJECT_ROOT / "audit.log"

FUNNEL_PORT = 8443
QR_VALIDITY_SECONDS = 900  # 15 minutos
PANEL_START_TIME = time.time()


def run_hidden(cmd, **kwargs):
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000
    if kwargs.get("text"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, **kwargs)


def popen_hidden(cmd, **kwargs):
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000
    return subprocess.Popen(cmd, **kwargs)


def format_duration(seconds: float) -> str:
    sec = max(0, int(seconds))
    hours, rem = divmod(sec, 3600)
    mins, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {mins:02d}m {secs:02d}s"
    return f"{mins}m {secs:02d}s"


class Tooltip:
    """Tooltip flotante limpio para mostrar texto completo en elementos truncados o iconos colapsados."""
    def __init__(self, widget: tk.Widget, text_getter, bg="#181C26", fg="#F3F4F6", border="#2E3648"):
        self.widget = widget
        self.text_getter = text_getter
        self.bg = bg
        self.fg = fg
        self.border = border
        self.tip_win: Optional[tk.Toplevel] = None
        self._after_id = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._unschedule()
        self._after_id = self.widget.after(320, self._show)

    def _unschedule(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self.tip_win:
            return
        txt = self.text_getter() if callable(self.text_getter) else str(self.text_getter or "")
        if not txt or not txt.strip():
            return
        try:
            x = self.widget.winfo_rootx() + 14
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self.tip_win = tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            frame = tk.Frame(tw, bg=self.bg, highlightbackground=self.border, highlightthickness=1, padx=10, pady=6)
            frame.pack()
            lbl = tk.Label(
                frame,
                text=txt,
                bg=self.bg,
                fg=self.fg,
                font=("Segoe UI", 9),
                justify="left",
                wraplength=420
            )
            lbl.pack()
            tw.wm_geometry(f"+{x}+{y}")
        except Exception:
            self._hide()

    def _hide(self, _event=None):
        self._unschedule()
        if self.tip_win:
            try:
                self.tip_win.destroy()
            except Exception:
                pass
            self.tip_win = None


class FlowFrame(tk.Frame):
    """
    Contenedor Flexbox-Wrap nativo para Tkinter:
    Acomoda sus widgets hijos de izquierda a derecha y los envuelve automáticamente
    en múltiples filas cuando el ancho de la ventana se reduce, evitando que ningún botón se corte.
    """
    def __init__(self, parent, bg="#131720", gap_x=8, gap_y=8, **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        self.gap_x = gap_x
        self.gap_y = gap_y
        self._flow_items: List[tk.Widget] = []
        self._last_width = -1
        self.bind("<Configure>", self._on_configure)

    def add_item(self, widget: tk.Widget):
        self._flow_items.append(widget)
        self.after_idle(self._reflow)

    def _on_configure(self, event):
        if event.width > 40 and abs(event.width - self._last_width) > 2:
            self._last_width = event.width
            self._reflow(avail_w=event.width)

    def _reflow(self, avail_w: Optional[int] = None):
        if not self._flow_items:
            return
        w_candidate = avail_w if (avail_w and avail_w > 40) else self.winfo_width()
        if w_candidate <= 40:
            try:
                w_candidate = max(self.master.winfo_width() - 32, 640)
            except Exception:
                w_candidate = 640
        w_limit = max(w_candidate, 240)
        x = 0
        y = 0
        row_h = 0

        for child in self._flow_items:
            if not child.winfo_exists():
                continue
            child.update_idletasks()
            cw = child.winfo_reqwidth()
            ch = child.winfo_reqheight()

            if x > 0 and (x + cw) > w_limit:
                x = 0
                y += row_h + self.gap_y
                row_h = 0

            child.place(x=x, y=y, width=cw, height=ch)
            x += cw + self.gap_x
            if ch > row_h:
                row_h = ch

        total_h = y + row_h + 2
        if self.winfo_reqheight() != total_h:
            self.configure(height=max(total_h, 28))


class ScrollableSection(tk.Frame):
    """
    Contenedor de sección con scroll vertical automático cuando el contenido supera la altura
    de la ventana (ej. en 800x600 o 680x500 con tarjetas apiladas), y expansión 100% en pantallas grandes.
    """
    def __init__(self, parent, bg="#0B0D11", **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style="Vertical.TScrollbar")
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vscroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._scroll_visible = False

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.inner.bind("<Configure>", self._on_inner_configure)

    def _update_scroll_state(self):
        self.inner.update_idletasks()
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        self.canvas.itemconfigure(self._win_id, width=cw)
        req_h = self.inner.winfo_reqheight()
        need_scroll = req_h > (ch + 4)

        if need_scroll and not self._scroll_visible:
            self.vscroll.pack(side="right", fill="y")
            self._scroll_visible = True
        elif not need_scroll and self._scroll_visible:
            self.vscroll.pack_forget()
            self._scroll_visible = False
            self.canvas.yview_moveto(0)

        self.canvas.configure(scrollregion=(0, 0, cw, max(ch, req_h)))

    def _on_canvas_configure(self, _event=None):
        self._update_scroll_state()

    def _on_inner_configure(self, _event=None):
        self._update_scroll_state()


class RoundedCard(tk.Canvas):
    """
    Tarjeta moderna con esquinas redondeadas, sombra sutil inferior y micro-interacción de borde en hover.
    """
    def __init__(
        self,
        parent,
        bg_outer="#0B0D11",
        bg_card="#131720",
        border_color="#222734",
        hover_border="#384156",
        shadow_color="#06070A",
        radius=12,
        padx=16,
        pady=13,
        **kwargs
    ):
        super().__init__(parent, bg=bg_outer, highlightthickness=0, bd=0, **kwargs)
        self.bg_card = bg_card
        self.border_color = border_color
        self.hover_border = hover_border
        self.shadow_color = shadow_color
        self._current_border = border_color
        self.radius = radius
        self.padx = padx
        self.pady = pady

        self.body = tk.Frame(self, bg=bg_card)
        self._win_id = self.create_window(padx, pady, window=self.body, anchor="nw")

        self.bind("<Configure>", self._on_canvas_configure)
        self.body.bind("<Configure>", self._on_body_configure)
        self.bind("<Enter>", self._on_hover_enter, add="+")
        self.bind("<Leave>", self._on_hover_leave, add="+")

    def _on_hover_enter(self, _event=None):
        self._current_border = self.hover_border
        self._redraw()

    def _on_hover_leave(self, _event=None):
        self._current_border = self.border_color
        self._redraw()

    def _redraw(self):
        w = max(self.winfo_width(), 40)
        h = max(self.winfo_height(), self.body.winfo_reqheight() + (self.pady * 2), 40)
        self._draw_rounded_rect(1, 1, w - 2, h - 3, self.radius)

    def _poly_points(self, x1, y1, x2, y2, r):
        return [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]

    def _draw_rounded_rect(self, x1, y1, x2, y2, r):
        self.delete("card_bg")
        if x2 - x1 < 2 * r or y2 - y1 < 2 * r:
            self.create_rectangle(x1, y1, x2, y2, fill=self.bg_card, outline=self._current_border, tags="card_bg")
            self.tag_lower("card_bg")
            return

        # Sombra sutil inferior (2px offset)
        sh_pts = self._poly_points(x1, y1 + 2, x2, y2 + 2, r)
        self.create_polygon(sh_pts, smooth=True, fill=self.shadow_color, outline="", tags="card_bg")

        pts = self._poly_points(x1, y1, x2, y2, r)
        self.create_polygon(
            pts,
            smooth=True,
            fill=self.bg_card,
            outline=self._current_border,
            width=1,
            tags="card_bg"
        )
        self.tag_lower("card_bg")

    def _on_body_configure(self, _event=None):
        req_h = self.body.winfo_reqheight() + (self.pady * 2) + 2
        if abs(self.winfo_height() - req_h) > 2:
            self.configure(height=req_h)

    def _on_canvas_configure(self, event):
        w = max(event.width, 40)
        h = max(event.height, self.body.winfo_reqheight() + (self.pady * 2) + 2, 40)
        self.itemconfigure(self._win_id, width=max(w - (self.padx * 2), 10))
        self._draw_rounded_rect(1, 1, w - 2, h - 3, self.radius)


class BridgeControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"MuseBridge v{BRIDGE_VERSION} — Centro de Control")
        self.root.geometry("1180x780")
        self.root.minsize(640, 480)

        for ico_candidate in (
            PROJECT_ROOT / "musebridge.ico",
            Path(getattr(sys, "_MEIPASS", str(PROJECT_ROOT))) / "musebridge.ico",
        ):
            if ico_candidate.exists():
                try:
                    self.root.iconbitmap(default=str(ico_candidate))
                    break
                except Exception:
                    pass

        self.local_port = get_configured_local_port()
        self.api_token = self.load_token_from_env()
        self.tailscale_domain = self.detect_tailscale_domain()

        self.permissions = load_permissions()
        if not self.permissions.get("installed_apps_catalog"):
            self.permissions = sync_scanned_apps(scan_installed_apps())

        self.theme_mode = self.permissions.get("ui_theme", "oscuro")
        self.apply_palette(self.theme_mode)

        # Tipografía consistente y limpia (estilo Linear / Configuración de Windows 11)
        self.f_title_xl = ("Segoe UI Semibold", 17)
        self.f_title_lg = ("Segoe UI Semibold", 15)
        self.f_title_md = ("Segoe UI Semibold", 11)
        self.f_kpi_val = ("Segoe UI Semibold", 14)
        self.f_sans = ("Segoe UI", 10)
        self.f_sans_bold = ("Segoe UI Semibold", 10)
        self.f_sans_sm = ("Segoe UI", 9)
        self.f_sans_xs = ("Segoe UI Semibold", 8)
        self.f_mono = ("Consolas", 9)

        # Estado de colapso de la barra lateral
        self.sidebar_collapsed = False
        self.sidebar_manual_override: Optional[bool] = None
        self._last_root_w = -1
        self._kpi_cols_mode = 0

        # Estado del QR
        self.qr_created_at = time.time()
        self.qr_expires_at = self.qr_created_at + QR_VALIDITY_SECONDS
        self.qr_image_tk = None
        self.last_pil_qr = None

        # Variables de la sección Puertos
        self.var_scan_start = tk.StringVar(value="8000")
        self.var_scan_end = tk.StringVar(value="8010")
        self.var_selected_port = tk.StringVar(value=str(self.local_port))

        # Filtros de apps y auditoría
        self.app_filter_mode = "all"
        self.app_search_var = tk.StringVar()
        self.app_search_var.trace_add("write", lambda *_: self.populate_apps_list())

        self.audit_filter_var = tk.StringVar()
        self.audit_filter_var.trace_add("write", lambda *_: self.refresh_audit_table(force=True))
        self._last_audit_size = -1

        self.current_section = "dashboard"
        self.nav_buttons: Dict[str, tk.Button] = {}
        self.nav_meta: List[Tuple[str, str, str]] = [
            ("dashboard", "⊞", "Tablero y Auditoría"),
            ("connection", "◈", "Alta por Código QR"),
            ("ports", "⇄", "Puertos de Red"),
            ("apps", "⛨", "Apps y Permisos"),
            ("files", "▤", "Archivos y Carpetas"),
            ("tutorial", "ⓘ", "Servicio y Guía"),
        ]
        self.sections: Dict[str, tk.Frame] = {}

        self.build_layout()
        self.switch_section("dashboard", animate=False)

        self.root.bind("<Configure>", self._on_root_window_resize)

        self.refresh_audit_table(force=True)
        self.refresh_ui_status()

        self.start_log_watcher()
        self.start_status_poller()
        self.start_qr_timer()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def apply_palette(self, mode: str):
        """Paleta sobria de alto contraste (Modo Oscuro Carbon / Modo Claro Slate)."""
        self.theme_mode = mode
        if mode == "oscuro":
            self.c_bg = "#0B0D11"
            self.c_surface = "#131720"
            self.c_surface2 = "#1B202C"
            self.c_surface2_hover = "#242B3A"
            self.c_sidebar = "#0E1117"
            self.c_navy = "#2563EB"
            self.c_navy_hover = "#3B82F6"
            self.c_navy_light = "#142242"
            self.c_red = "#DC2626"
            self.c_red_hover = "#EF4444"
            self.c_red_light = "#2D1418"
            self.c_green = "#10B981"
            self.c_green_light = "#0E2922"
            self.c_amber = "#F59E0B"
            self.c_amber_light = "#2C2110"
            self.c_text = "#F3F4F6"
            self.c_text2 = "#9CA3AF"
            self.c_text3 = "#6B7280"
            self.c_border = "#222734"
            self.c_border_hover = "#384156"
            self.c_shadow = "#050608"
            self.c_row_alt = "#161B25"
            self.c_row_ok = "#11241E"
            self.c_tip_bg = "#1A202C"
        else:
            self.c_bg = "#F4F5F7"
            self.c_surface = "#FFFFFF"
            self.c_surface2 = "#E9ECF2"
            self.c_surface2_hover = "#DCE1EB"
            self.c_sidebar = "#111827"
            self.c_navy = "#1D4ED8"
            self.c_navy_hover = "#2563EB"
            self.c_navy_light = "#EFF6FF"
            self.c_red = "#DC2626"
            self.c_red_hover = "#B91C1C"
            self.c_red_light = "#FEE2E2"
            self.c_green = "#059669"
            self.c_green_light = "#D1FAE5"
            self.c_amber = "#D97706"
            self.c_amber_light = "#FEF3C7"
            self.c_text = "#111827"
            self.c_text2 = "#4B5563"
            self.c_text3 = "#6B7280"
            self.c_border = "#E2E5EC"
            self.c_border_hover = "#CBD2E0"
            self.c_shadow = "#E6E9F0"
            self.c_row_alt = "#F9FAFB"
            self.c_row_ok = "#ECFDF5"
            self.c_tip_bg = "#111827"

        self.root.configure(bg=self.c_bg)
        self.setup_ttk_styles()

    def setup_ttk_styles(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Vertical.TScrollbar",
            background=self.c_surface2,
            troughcolor=self.c_bg,
            bordercolor=self.c_border,
            arrowcolor=self.c_text2,
            width=10
        )
        style.configure(
            "Horizontal.TScrollbar",
            background=self.c_surface2,
            troughcolor=self.c_bg,
            bordercolor=self.c_border,
            arrowcolor=self.c_text2,
            width=10
        )
        style.configure(
            "Audit.Treeview",
            background=self.c_surface,
            foreground=self.c_text,
            fieldbackground=self.c_surface,
            rowheight=30,
            font=("Segoe UI", 9),
            borderwidth=0
        )
        style.configure(
            "Audit.Treeview.Heading",
            background=self.c_surface2,
            foreground=self.c_text2,
            font=("Segoe UI Semibold", 9),
            relief="flat",
            padding=(10, 7)
        )
        style.map(
            "Audit.Treeview",
            background=[("selected", self.c_navy)],
            foreground=[("selected", "#FFFFFF")]
        )

    def make_button(
        self,
        parent,
        text: str,
        command,
        variant: str = "secondary",
        padx: int = 13,
        pady: int = 6,
        font=None,
        tooltip: Optional[str] = None
    ) -> tk.Button:
        """Crea un botón con micro-interacciones de hover, estado presionado y tooltip."""
        if variant == "primary":
            bg_norm, fg_norm = self.c_navy, "#FFFFFF"
            bg_hov, bg_act = self.c_navy_hover, "#1E40AF"
        elif variant == "danger":
            bg_norm, fg_norm = self.c_red, "#FFFFFF"
            bg_hov, bg_act = self.c_red_hover, "#991B1B"
        elif variant == "danger_subtle":
            bg_norm, fg_norm = self.c_red_light, self.c_red
            bg_hov, bg_act = self.c_red, "#FFFFFF"
        elif variant == "success":
            bg_norm, fg_norm = self.c_green, "#FFFFFF"
            bg_hov, bg_act = "#059669", "#047857"
        elif variant == "success_subtle":
            bg_norm, fg_norm = self.c_green_light, self.c_green
            bg_hov, bg_act = self.c_surface2_hover, self.c_green_light
        elif variant == "accent_subtle":
            bg_norm, fg_norm = self.c_navy_light, ("#60A5FA" if self.theme_mode == "oscuro" else self.c_navy)
            bg_hov, bg_act = self.c_surface2_hover, self.c_navy_light
        else:
            bg_norm, fg_norm = self.c_surface2, self.c_text
            bg_hov, bg_act = self.c_surface2_hover, self.c_border

        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg_norm,
            fg=fg_norm,
            activebackground=bg_act,
            activeforeground="#FFFFFF" if variant in ("primary", "danger", "success") else fg_norm,
            font=font or self.f_sans_bold,
            relief="flat",
            bd=0,
            padx=padx,
            pady=pady,
            cursor="hand2"
        )
        btn._bg_norm = bg_norm
        btn._bg_hov = bg_hov
        btn.bind("<Enter>", lambda _e: btn.cget("state") != "disabled" and btn.config(bg=btn._bg_hov), add="+")
        btn.bind("<Leave>", lambda _e: btn.cget("state") != "disabled" and btn.config(bg=btn._bg_norm), add="+")
        if tooltip:
            Tooltip(btn, tooltip, bg=self.c_tip_bg, fg="#F3F4F6", border=self.c_border_hover)
        return btn

    def attach_treeview_tooltip(self, tree: ttk.Treeview):
        """Muestra un tooltip con los valores completos de la fila bajo el cursor en tablas estrechas."""
        tip_win: List[Optional[tk.Toplevel]] = [None]
        last_item: List[str] = [""]

        def _hide(_e=None):
            last_item[0] = ""
            if tip_win[0]:
                try:
                    tip_win[0].destroy()
                except Exception:
                    pass
                tip_win[0] = None

        def _motion(event):
            row_id = tree.identify_row(event.y)
            if not row_id:
                _hide()
                return
            if row_id == last_item[0]:
                return
            _hide()
            last_item[0] = row_id
            vals = tree.item(row_id, "values")
            if not vals:
                return
            text = "  │  ".join(str(v) for v in vals if str(v))
            try:
                tw = tk.Toplevel(tree)
                tw.wm_overrideredirect(True)
                tw.wm_attributes("-topmost", True)
                f = tk.Frame(tw, bg=self.c_tip_bg, highlightbackground=self.c_border_hover, highlightthickness=1, padx=10, pady=6)
                f.pack()
                tk.Label(f, text=text, bg=self.c_tip_bg, fg="#F3F4F6", font=("Consolas", 8), wraplength=540, justify="left").pack()
                tw.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 16}")
                tip_win[0] = tw
            except Exception:
                _hide()

        tree.bind("<Motion>", _motion, add="+")
        tree.bind("<Leave>", _hide, add="+")
        tree.bind("<ButtonPress>", _hide, add="+")

    def toggle_theme_mode(self):
        new_mode = "oscuro" if self.theme_mode == "claro" else "claro"
        self.permissions["ui_theme"] = new_mode
        save_permissions(self.permissions)
        curr_sec = self.current_section
        self.apply_palette(new_mode)
        for child in self.root.winfo_children():
            child.destroy()
        self.nav_buttons.clear()
        self.sections.clear()
        self._kpi_cols_mode = 0
        self.build_layout()
        self.switch_section(curr_sec, animate=False)
        self._last_audit_size = -1
        self.refresh_audit_table(force=True)
        self.refresh_ui_status()

    def load_token_from_env(self) -> str:
        if ENV_FILE.exists():
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("API_SECRET_TOKEN="):
                        return line.strip().split("=", 1)[1]
        token = secrets.token_hex(32)
        self.save_token_to_env(token)
        return token

    def save_token_to_env(self, token: str):
        lines = []
        token_written = False
        if ENV_FILE.exists():
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("API_SECRET_TOKEN="):
                        lines.append(f"API_SECRET_TOKEN={token}\n")
                        token_written = True
                    else:
                        lines.append(line)
        if not token_written:
            lines.append(f"API_SECRET_TOKEN={token}\n")
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)

    def detect_tailscale_domain(self) -> str:
        try:
            res = run_hidden(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=3, check=False)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                dns_name = data.get("Self", {}).get("DNSName", "").rstrip(".")
                if dns_name:
                    return dns_name
        except Exception:
            pass
        return "mi-equipo.ts.net"

    def get_funnel_url(self) -> str:
        return f"https://{self.tailscale_domain}:{FUNNEL_PORT}"

    def get_display_funnel_url(self) -> str:
        dom = self.tailscale_domain
        if "aodental" in dom.lower():
            parts = dom.split(".", 1)
            dom = f"musebridge.{parts[1]}" if len(parts) > 1 else "musebridge.ts.net"
        return f"https://{dom}:{FUNNEL_PORT}"

    # =========================================================================
    # MOTOR RESPONSIVE DE VENTANA (SIDEBAR COLAPSABLE, TOPBAR Y KPI REFLOW)
    # =========================================================================
    def _on_root_window_resize(self, event):
        if event.widget is not self.root:
            return
        w = event.width
        if abs(w - self._last_root_w) < 8:
            return
        self._last_root_w = w

        # Auto-colapsar barra lateral cuando el ancho total < 940px (a menos que el usuario la haya fijado)
        if self.sidebar_manual_override is None:
            should_collapse = (w < 940)
            if should_collapse != self.sidebar_collapsed:
                self.set_sidebar_collapsed(should_collapse)

    def toggle_sidebar_manual(self):
        new_state = not self.sidebar_collapsed
        self.sidebar_manual_override = new_state
        self.set_sidebar_collapsed(new_state)

    def set_sidebar_collapsed(self, collapsed: bool):
        self.sidebar_collapsed = collapsed
        target_w = 58 if collapsed else 224
        self.sidebar.configure(width=target_w)

        if collapsed:
            self.lbl_brand_title.pack_forget()
            self.lbl_brand_sub.pack_forget()
            self.nav_frame.configure(padx=6)
            for sec_id, icon_ch, _full_txt in self.nav_meta:
                btn = self.nav_buttons.get(sec_id)
                if btn:
                    btn.configure(text=icon_ch, anchor="center", padx=4)
            if hasattr(self, "footer_box"):
                self.footer_box.configure(padx=2)
            self.lbl_sidebar_footer.configure(
                text=f":{self.local_port}\nv{BRIDGE_VERSION}",
                anchor="center",
                justify="center"
            )
            self.lbl_sidebar_footer.pack_configure(anchor="center")
        else:
            self.lbl_brand_title.pack(anchor="w")
            self.lbl_brand_sub.pack(anchor="w", pady=(2, 0))
            self.nav_frame.configure(padx=10)
            for sec_id, icon_ch, full_txt in self.nav_meta:
                btn = self.nav_buttons.get(sec_id)
                if btn:
                    btn.configure(text=f"{icon_ch}   {full_txt}", anchor="w", padx=12)
            if hasattr(self, "footer_box"):
                self.footer_box.configure(padx=12)
            self.lbl_sidebar_footer.configure(
                text=f"Local :{self.local_port} · Funnel :{FUNNEL_PORT}\nMuseBridge v{BRIDGE_VERSION}",
                anchor="w",
                justify="left"
            )
            self.lbl_sidebar_footer.pack_configure(anchor="w")

    # =========================================================================
    # ESTRUCTURA GENERAL (SIDEBAR + TOPBAR ENVOLVENTE + CONTENEDOR DE VISTAS)
    # =========================================================================
    def build_layout(self):
        self.banner_emergency = tk.Label(
            self.root,
            text="PARADA DE EMERGENCIA ACTIVA — SERVIDOR APAGADO Y TÚNEL FUNNEL CORTADO",
            bg=self.c_red,
            fg="#FFFFFF",
            font=self.f_sans_bold,
            pady=7
        )

        # Banner de conflicto de puerto (Error 10048)
        self.banner_port_conflict = tk.Frame(self.root, bg=self.c_amber_light, padx=16, pady=6, highlightbackground=self.c_amber, highlightthickness=1)
        self.lbl_port_conflict_msg = tk.Label(
            self.banner_port_conflict,
            text="El puerto local actual está ocupado por otro proceso (Error 10048).",
            bg=self.c_amber_light,
            fg=self.c_amber,
            font=self.f_sans_bold
        )
        self.lbl_port_conflict_msg.pack(side="left")
        self.make_button(
            self.banner_port_conflict,
            text="⇄  Elegir Puerto Libre",
            command=self.jump_to_ports_and_scan,
            variant="primary",
            padx=10,
            pady=3,
            font=self.f_sans_sm
        ).pack(side="right")

        self.body_wrap = tk.Frame(self.root, bg=self.c_bg)
        self.body_wrap.pack(fill="both", expand=True)

        # 1. Barra Lateral Izquierda Colapsable
        self.sidebar = tk.Frame(self.body_wrap, bg=self.c_sidebar, width=220)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Línea divisoria derecha de la barra lateral
        tk.Frame(self.body_wrap, bg=self.c_border, width=1).pack(side="left", fill="y")

        logo_box = tk.Frame(self.sidebar, bg=self.c_sidebar, padx=14, pady=16)
        logo_box.pack(fill="x")

        top_logo_row = tk.Frame(logo_box, bg=self.c_sidebar)
        top_logo_row.pack(fill="x")

        self.btn_collapse_sidebar = tk.Button(
            top_logo_row,
            text="☰",
            command=self.toggle_sidebar_manual,
            bg=self.c_sidebar,
            fg="#9CA3AF",
            activebackground="#1F2937",
            activeforeground="#FFFFFF",
            font=("Segoe UI", 11),
            relief="flat",
            bd=0,
            padx=6,
            pady=2,
            cursor="hand2"
        )
        self.btn_collapse_sidebar.pack(side="left", padx=(0, 8))
        Tooltip(self.btn_collapse_sidebar, "Contraer / Expandir barra lateral", bg=self.c_tip_bg)

        brand_text_wrap = tk.Frame(top_logo_row, bg=self.c_sidebar)
        brand_text_wrap.pack(side="left", fill="x", expand=True)

        self.lbl_brand_title = tk.Label(
            brand_text_wrap,
            text="MuseBridge",
            font=("Segoe UI Semibold", 15),
            bg=self.c_sidebar,
            fg="#FFFFFF"
        )
        self.lbl_brand_title.pack(anchor="w")

        self.lbl_brand_sub = tk.Label(
            brand_text_wrap,
            text=f"CONTROL CENTER · v{BRIDGE_VERSION}",
            font=("Segoe UI Semibold", 7),
            bg=self.c_sidebar,
            fg="#6B7280"
        )
        self.lbl_brand_sub.pack(anchor="w", pady=(2, 0))

        tk.Frame(self.sidebar, bg="#1F2430", height=1).pack(fill="x", pady=(0, 10))

        self.nav_frame = tk.Frame(self.sidebar, bg=self.c_sidebar, padx=10)
        self.nav_frame.pack(fill="both", expand=True)

        for sec_id, icon_ch, full_txt in self.nav_meta:
            btn = tk.Button(
                self.nav_frame,
                text=f"{icon_ch}   {full_txt}",
                command=lambda s=sec_id: self.switch_section(s, animate=True),
                bg=self.c_sidebar,
                fg="#9CA3AF",
                activebackground="#1E293B",
                activeforeground="#FFFFFF",
                font=self.f_sans,
                relief="flat",
                bd=0,
                anchor="w",
                padx=12,
                pady=9,
                cursor="hand2"
            )
            btn.pack(fill="x", pady=2)
            btn.bind("<Enter>", lambda _e, b=btn, s=sec_id: s != self.current_section and b.config(bg="#171C26", fg="#E5E7EB"), add="+")
            btn.bind("<Leave>", lambda _e, b=btn, s=sec_id: s != self.current_section and b.config(bg=self.c_sidebar, fg="#9CA3AF"), add="+")
            Tooltip(btn, full_txt, bg=self.c_tip_bg)
            self.nav_buttons[sec_id] = btn

        tk.Frame(self.sidebar, bg="#1F2430", height=1).pack(fill="x", side="bottom", pady=(0, 48))
        self.footer_box = tk.Frame(self.sidebar, bg=self.c_sidebar, padx=12, pady=8)
        self.footer_box.pack(side="bottom", fill="x")
        self.lbl_sidebar_footer = tk.Label(
            self.footer_box,
            text=f"Local :{self.local_port} · Funnel :{FUNNEL_PORT}\nMuseBridge v{BRIDGE_VERSION}",
            font=("Consolas", 8),
            bg=self.c_sidebar,
            fg="#9CA3AF",
            anchor="w",
            justify="left"
        )
        self.lbl_sidebar_footer.pack(anchor="w", fill="x")
        Tooltip(
            self.lbl_sidebar_footer,
            lambda: f"Servidor Local: 127.0.0.1:{self.local_port}  │  Tailscale Funnel: :{FUNNEL_PORT}  │  Versión: v{BRIDGE_VERSION}",
            bg=self.c_tip_bg
        )

        # 2. Área Principal
        self.main_area = tk.Frame(self.body_wrap, bg=self.c_bg)
        self.main_area.pack(side="left", fill="both", expand=True)

        # Topbar auto-envolvente (1 fila en ventana ancha, 2 filas en ventana estrecha)
        self.topbar = tk.Frame(self.main_area, bg=self.c_surface, padx=18, pady=10)
        self.topbar.pack(fill="x")
        tk.Frame(self.main_area, bg=self.c_border, height=1).pack(fill="x")

        self.topbar_left = tk.Frame(self.topbar, bg=self.c_surface)
        self.topbar_right = tk.Frame(self.topbar, bg=self.c_surface)

        self.lbl_section_title = tk.Label(
            self.topbar_left,
            text="Tablero y Auditoría",
            font=self.f_title_lg,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        )
        self.lbl_section_title.pack(side="left", padx=(0, 10))

        self.lbl_top_badge = tk.Label(
            self.topbar_left,
            text=" ● Verificando... ",
            font=("Segoe UI Semibold", 9),
            bg=self.c_amber_light,
            fg=self.c_amber,
            padx=10,
            pady=3
        )
        self.lbl_top_badge.pack(side="left")
        Tooltip(
            self.lbl_top_badge,
            lambda: f"Estado actual del servidor local (:{self.local_port}) y del túnel público (:{FUNNEL_PORT})",
            bg=self.c_tip_bg
        )

        # Botón de Emergencia DETENER CONEXIÓN (Kill Switch) siempre completo y legible
        self.btn_kill_switch = self.make_button(
            self.topbar_right,
            text="⏻  DETENER CONEXIÓN",
            command=self.trigger_kill_switch,
            variant="danger",
            padx=13,
            pady=6,
            tooltip="Interruptor de emergencia (Kill Switch): Apaga el servidor local y cierra el túnel Funnel de inmediato."
        )
        self.btn_kill_switch.pack(side="right")

        theme_label = "☀ Claro" if self.theme_mode == "oscuro" else "☾ Oscuro"
        self.btn_theme = self.make_button(
            self.topbar_right,
            text=theme_label,
            command=self.toggle_theme_mode,
            variant="secondary",
            padx=10,
            pady=6,
            font=self.f_sans_sm,
            tooltip="Alternar entre Modo Oscuro (Carbon) y Modo Claro (Slate)"
        )
        self.btn_theme.pack(side="right", padx=(0, 8))

        # Empaquetar primero topbar_right para que jamás se recorte en modo de 1 fila
        self.topbar_right.pack(side="right")
        self.topbar_left.pack(side="left", fill="x", expand=True)
        self._topbar_stacked = False
        self.topbar.bind("<Configure>", self._on_topbar_configure)

        self.content_holder = tk.Frame(self.main_area, bg=self.c_bg, padx=16, pady=12)
        self.content_holder.pack(fill="both", expand=True)

        self.section_scrollers: Dict[str, ScrollableSection] = {}
        for sec_id in ("dashboard", "connection", "ports", "apps", "files", "tutorial"):
            scroller = ScrollableSection(self.content_holder, bg=self.c_bg)
            self.sections[sec_id] = scroller
            self.section_scrollers[sec_id] = scroller

        self.build_dashboard_section(self.section_scrollers["dashboard"].inner)
        self.build_connection_section(self.section_scrollers["connection"].inner)
        self.build_ports_section(self.section_scrollers["ports"].inner)
        self.build_apps_section(self.section_scrollers["apps"].inner)
        self.build_files_section(self.section_scrollers["files"].inner)
        self.build_tutorial_section(self.section_scrollers["tutorial"].inner)

        self.update_kill_switch_ui()

    def _on_topbar_configure(self, event):
        should_stack = (event.width < 880)
        if should_stack != self._topbar_stacked:
            self._topbar_stacked = should_stack
            self.topbar_left.pack_forget()
            self.topbar_right.pack_forget()
            if should_stack:
                self.topbar_left.pack(side="top", fill="x", anchor="w", pady=(0, 6))
                self.topbar_right.pack(side="top", fill="x", anchor="e")
            else:
                self.topbar_right.pack(side="right")
                self.topbar_left.pack(side="left", fill="x", expand=True)

    def switch_section(self, sec_id: str, animate: bool = True):
        titles = {
            "dashboard": "Tablero de Estado y Auditoría",
            "connection": "Alta por Código QR y Vinculación",
            "ports": "Gestión y Selector de Puertos",
            "apps": "Aplicaciones Instaladas y Permisos",
            "files": "Funciones de Archivos y Carpetas",
            "tutorial": "Servicio Vigilante y Documentación",
        }
        self.current_section = sec_id
        self.lbl_section_title.config(text=titles.get(sec_id, "Tablero de Control"))

        for k, btn in self.nav_buttons.items():
            if k == sec_id:
                btn.config(bg=self.c_navy, fg="#FFFFFF", font=self.f_sans_bold)
            else:
                btn.config(bg=self.c_sidebar, fg="#9CA3AF", font=self.f_sans)

        for k, frame in self.sections.items():
            frame.pack_forget()
            frame.place_forget()

        target = self.sections[sec_id]
        if sec_id == "ports":
            self.refresh_ports_section_status()
        self.refresh_kpi_counters()

        if animate:
            self._animate_section_entry(target)
        else:
            target.pack(fill="both", expand=True)

    def show_section(self, sec_id: str):
        self.switch_section(sec_id, animate=True)

    def jump_to_ports_and_scan(self):
        self.switch_section("ports", animate=True)
        self.run_port_range_scan()

    def toggle_theme(self):
        self.toggle_theme_mode()

    def _animate_section_entry(self, target_frame: tk.Frame):
        """Transición sutil y rápida de desplazamiento horizontal (4 cuadros en 40 ms)."""
        offsets = [18, 9, 3, 0]

        def _step(idx: int):
            if self.sections.get(self.current_section) is not target_frame:
                return
            off = offsets[idx]
            if off > 0:
                target_frame.place(x=off, y=0, relwidth=1.0, relheight=1.0)
                self.root.after(11, lambda: _step(idx + 1))
            else:
                target_frame.place_forget()
                target_frame.pack(fill="both", expand=True)

        _step(0)

    def _bind_responsive_split(self, grid_frame: tk.Frame, left_widget: tk.Widget, right_widget: tk.Widget, breakpoint: int = 680):
        """Convierte automáticamente un layout de 2 columnas en 1 columna apilada cuando el ancho es menor que breakpoint."""
        state = {"stacked": None}

        def _on_cfg(event):
            is_narrow = (event.width < breakpoint)
            if is_narrow == state["stacked"]:
                return
            state["stacked"] = is_narrow
            left_widget.grid_forget()
            right_widget.grid_forget()
            if is_narrow:
                grid_frame.columnconfigure(0, weight=1, uniform="")
                grid_frame.columnconfigure(1, weight=0, uniform="")
                grid_frame.rowconfigure(0, weight=1)
                grid_frame.rowconfigure(1, weight=1)
                left_widget.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0, 8))
                right_widget.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 0))
            else:
                grid_frame.rowconfigure(0, weight=1)
                grid_frame.rowconfigure(1, weight=0)
                grid_frame.columnconfigure(0, weight=1, uniform="split")
                grid_frame.columnconfigure(1, weight=1, uniform="split")
                left_widget.grid(row=0, column=0, sticky="nsew", padx=(0, 7), pady=0)
                right_widget.grid(row=0, column=1, sticky="nsew", padx=(7, 0), pady=0)

        grid_frame.bind("<Configure>", _on_cfg)

    # =========================================================================
    # SECCIÓN 1: TABLERO DE ESTADO (KPI REFLOWABLE + FLOW BUTTONS + TABLA AUDITORÍA)
    # =========================================================================
    def build_dashboard_section(self, parent: tk.Frame):
        self.stats_row = tk.Frame(parent, bg=self.c_bg)
        self.stats_row.pack(fill="x", pady=(0, 12))

        self.kpi_cards: List[RoundedCard] = []

        # KPI 1: Puente Local + Versión + Uptime
        c1 = RoundedCard(
            self.stats_row, bg_outer=self.c_bg, bg_card=self.c_surface,
            border_color=self.c_border, hover_border=self.c_border_hover, shadow_color=self.c_shadow, padx=14, pady=10
        )
        tk.Label(c1.body, text=f"PUENTE LOCAL · v{BRIDGE_VERSION}", font=self.f_sans_xs, bg=self.c_surface, fg=self.c_text3, anchor="w", justify="left").pack(anchor="w", fill="x")
        self.lbl_kpi_server = tk.Label(c1.body, text="Activo", font=self.f_kpi_val, bg=self.c_surface, fg=self.c_green, anchor="w", justify="left")
        self.lbl_kpi_server.pack(anchor="w", fill="x", pady=(2, 1))
        self.lbl_kpi_uptime = tk.Label(c1.body, text=f"127.0.0.1:{self.local_port} · Uptime: 0m", font=self.f_sans_sm, bg=self.c_surface, fg=self.c_text2, anchor="w", justify="left")
        self.lbl_kpi_uptime.pack(anchor="w", fill="x")
        Tooltip(c1, lambda: f"Puente Local: {self.lbl_kpi_server.cget('text')} ({self.lbl_kpi_uptime.cget('text')})", bg=self.c_tip_bg)
        self.kpi_cards.append(c1)

        # KPI 2: Tailscale Funnel 8443
        c2 = RoundedCard(
            self.stats_row, bg_outer=self.c_bg, bg_card=self.c_surface,
            border_color=self.c_border, hover_border=self.c_border_hover, shadow_color=self.c_shadow, padx=14, pady=10
        )
        tk.Label(c2.body, text="TAILSCALE FUNNEL :8443", font=self.f_sans_xs, bg=self.c_surface, fg=self.c_text3, anchor="w", justify="left").pack(anchor="w", fill="x")
        self.lbl_kpi_funnel = tk.Label(c2.body, text="Verificando...", font=self.f_kpi_val, bg=self.c_surface, fg=self.c_navy, anchor="w", justify="left")
        self.lbl_kpi_funnel.pack(anchor="w", fill="x", pady=(2, 1))
        self.lbl_kpi_funnel_sub = tk.Label(c2.body, text=f"Proxy HTTPS -> :{self.local_port}", font=self.f_sans_sm, bg=self.c_surface, fg=self.c_text2, anchor="w", justify="left")
        self.lbl_kpi_funnel_sub.pack(anchor="w", fill="x")
        Tooltip(c2, lambda: f"Tailscale Funnel: {self.lbl_kpi_funnel.cget('text')} ({self.get_display_funnel_url()})", bg=self.c_tip_bg)
        self.kpi_cards.append(c2)

        # KPI 3: CPU / Memoria RAM / Resolución
        c3 = RoundedCard(
            self.stats_row, bg_outer=self.c_bg, bg_card=self.c_surface,
            border_color=self.c_border, hover_border=self.c_border_hover, shadow_color=self.c_shadow, padx=14, pady=10
        )
        tk.Label(c3.body, text="RECURSOS (CPU / RAM)", font=self.f_sans_xs, bg=self.c_surface, fg=self.c_text3, anchor="w", justify="left").pack(anchor="w", fill="x")
        self.lbl_kpi_resources = tk.Label(c3.body, text="CPU 0% · RAM 0%", font=self.f_kpi_val, bg=self.c_surface, fg=self.c_text, anchor="w", justify="left")
        self.lbl_kpi_resources.pack(anchor="w", fill="x", pady=(2, 1))
        self.lbl_kpi_resources_sub = tk.Label(c3.body, text="Captura Nativa: 1920x1080", font=self.f_sans_sm, bg=self.c_surface, fg=self.c_text2, anchor="w", justify="left")
        self.lbl_kpi_resources_sub.pack(anchor="w", fill="x")
        Tooltip(c3, lambda: f"Recursos del Sistema: {self.lbl_kpi_resources.cget('text')} — {self.lbl_kpi_resources_sub.cget('text')}", bg=self.c_tip_bg)
        self.kpi_cards.append(c3)

        # KPI 4: Estado de Permisos y Último Chequeo de Salud
        c4 = RoundedCard(
            self.stats_row, bg_outer=self.c_bg, bg_card=self.c_surface,
            border_color=self.c_border, hover_border=self.c_border_hover, shadow_color=self.c_shadow, padx=14, pady=10
        )
        tk.Label(c4.body, text="PERMISOS Y SALUD", font=self.f_sans_xs, bg=self.c_surface, fg=self.c_text3, anchor="w", justify="left").pack(anchor="w", fill="x")
        self.lbl_kpi_perms = tk.Label(c4.body, text="0 Apps · 0 Rutas", font=self.f_kpi_val, bg=self.c_surface, fg=self.c_text, anchor="w", justify="left")
        self.lbl_kpi_perms.pack(anchor="w", fill="x", pady=(2, 1))
        self.lbl_kpi_health_time = tk.Label(c4.body, text="Último chequeo: --:--:--", font=self.f_sans_sm, bg=self.c_surface, fg=self.c_text2, anchor="w", justify="left")
        self.lbl_kpi_health_time.pack(anchor="w", fill="x")
        Tooltip(c4, lambda: f"Permisos activos: {self.lbl_kpi_perms.cget('text')} — {self.lbl_kpi_health_time.cget('text')}", bg=self.c_tip_bg)
        self.kpi_cards.append(c4)

        self.stats_row.bind("<Configure>", self._on_stats_row_configure)
        self._layout_kpi_grid(4)

        # Tarjeta de Controles Rápidos con FlowFrame (los botones se envuelven en varias filas si la ventana es pequeña)
        ctrl_card = RoundedCard(
            parent, bg_outer=self.c_bg, bg_card=self.c_surface,
            border_color=self.c_border, hover_border=self.c_border_hover, shadow_color=self.c_shadow, padx=16, pady=11
        )
        ctrl_card.pack(fill="x", pady=(0, 12))

        top_ctrl = tk.Frame(ctrl_card.body, bg=self.c_surface)
        top_ctrl.pack(fill="x", pady=(0, 8))

        tk.Label(
            top_ctrl,
            text="Operación del Puente y Auto-Recuperación",
            font=self.f_title_md,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        ).pack(anchor="w")

        self.lbl_perm_pills = tk.Label(
            top_ctrl,
            text="Permisos: Verificando...",
            font=("Segoe UI", 8),
            bg=self.c_navy_light,
            fg="#60A5FA" if self.theme_mode == "oscuro" else self.c_navy,
            padx=9,
            pady=2,
            anchor="w",
            justify="left"
        )
        self.lbl_perm_pills.pack(anchor="w", pady=(4, 0))
        Tooltip(self.lbl_perm_pills, lambda: self.lbl_perm_pills.cget("text"), bg=self.c_tip_bg)

        self.ops_flow = FlowFrame(ctrl_card.body, bg=self.c_surface, gap_x=8, gap_y=8)
        self.ops_flow.pack(fill="x")

        self.btn_toggle_server = self.make_button(
            self.ops_flow,
            text="▸  Iniciar Servidor Local",
            command=self.toggle_server,
            variant="primary",
            tooltip="Inicia o detiene el servidor local del puente"
        )
        self.ops_flow.add_item(self.btn_toggle_server)

        self.btn_restart_server = self.make_button(
            self.ops_flow,
            text="⟳  Reiniciar Puente",
            command=self.restart_server_keep_funnel,
            variant="secondary",
            tooltip="Reinicia el proceso Uvicorn local conservando activo el túnel Tailscale Funnel (:8443)"
        )
        self.ops_flow.add_item(self.btn_restart_server)

        self.btn_toggle_funnel = self.make_button(
            self.ops_flow,
            text=f"🌐  Activar Funnel (:{FUNNEL_PORT})",
            command=self.toggle_funnel,
            variant="secondary",
            tooltip=f"Conecta o desconecta el túnel público Tailscale Funnel en el puerto {FUNNEL_PORT}"
        )
        self.ops_flow.add_item(self.btn_toggle_funnel)

        btn_ports_jump = self.make_button(
            self.ops_flow,
            text="⇄  Cambiar Puerto",
            command=lambda: self.switch_section("ports"),
            variant="accent_subtle",
            tooltip="Abrir el explorador y selector de puertos locales (8000–8010)"
        )
        self.ops_flow.add_item(btn_ports_jump)

        btn_health_now = self.make_button(
            self.ops_flow,
            text="✓  Chequeo de Salud",
            command=self.run_manual_health_check,
            variant="success_subtle",
            tooltip="Ejecuta una verificación completa de salud del servidor, CPU/RAM y Funnel :8443"
        )
        self.ops_flow.add_item(btn_health_now)

        btn_qr_jump = self.make_button(
            self.ops_flow,
            text="◈  Código QR",
            command=lambda: self.switch_section("connection"),
            variant="secondary",
            tooltip="Ver el código QR de vinculación y credenciales para Muse AI"
        )
        self.ops_flow.add_item(btn_qr_jump)

        # Tabla Estructurada de Auditoría con Scroll Horizontal y Vertical
        audit_card = tk.Frame(parent, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=16, pady=12)
        audit_card.pack(fill="both", expand=True)

        audit_hdr = tk.Frame(audit_card, bg=self.c_surface)
        audit_hdr.pack(fill="x", pady=(0, 8))

        filter_box = tk.Frame(audit_hdr, bg=self.c_surface2, highlightbackground=self.c_border, highlightthickness=1, padx=8, pady=2)
        filter_box.pack(side="right")
        tk.Label(filter_box, text="⌕ Filtrar:", font=self.f_sans_sm, bg=self.c_surface2, fg=self.c_text2).pack(side="left")
        tk.Entry(
            filter_box,
            textvariable=self.audit_filter_var,
            bg=self.c_surface2,
            fg=self.c_text,
            insertbackground=self.c_text,
            font=self.f_sans_sm,
            relief="flat",
            width=18
        ).pack(side="left", padx=4)

        tk.Label(
            audit_hdr,
            text="Registro de Auditoría en Vivo",
            font=self.f_title_md,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        ).pack(side="left")

        table_wrap = tk.Frame(audit_card, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1)
        table_wrap.pack(fill="both", expand=True)
        table_wrap.rowconfigure(0, weight=1)
        table_wrap.columnconfigure(0, weight=1)

        cols = ("timestamp", "endpoint", "action", "status", "details")
        self.audit_tree = ttk.Treeview(
            table_wrap,
            columns=cols,
            show="headings",
            style="Audit.Treeview",
            height=9
        )
        self.audit_tree.heading("timestamp", text="Fecha y Hora")
        self.audit_tree.heading("endpoint", text="Endpoint")
        self.audit_tree.heading("action", text="Acción")
        self.audit_tree.heading("status", text="Resultado")
        self.audit_tree.heading("details", text="Detalles Verificados")

        self.audit_tree.column("timestamp", width=155, minwidth=140, anchor="w", stretch=False)
        self.audit_tree.column("endpoint", width=140, minwidth=120, anchor="w", stretch=False)
        self.audit_tree.column("action", width=180, minwidth=150, anchor="w", stretch=False)
        self.audit_tree.column("status", width=95, minwidth=85, anchor="center", stretch=False)
        self.audit_tree.column("details", width=520, minwidth=360, anchor="w", stretch=True)

        self.audit_tree.tag_configure("SUCCESS", foreground=self.c_green)
        self.audit_tree.tag_configure("FORBIDDEN", foreground=self.c_amber)
        self.audit_tree.tag_configure("ERROR", foreground=self.c_red)

        tree_yscroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.audit_tree.yview, style="Vertical.TScrollbar")
        tree_xscroll = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.audit_tree.xview, style="Horizontal.TScrollbar")
        self.audit_tree.configure(yscrollcommand=tree_yscroll.set, xscrollcommand=tree_xscroll.set)

        self.audit_tree.grid(row=0, column=0, sticky="nsew")
        tree_yscroll.grid(row=0, column=1, sticky="ns")
        tree_xscroll.grid(row=1, column=0, sticky="ew")

        self.attach_treeview_tooltip(self.audit_tree)

    def _on_stats_row_configure(self, event):
        w = event.width
        if w >= 920:
            cols = 4
        elif w >= 380:
            cols = 2
        else:
            cols = 1
        if cols != self._kpi_cols_mode:
            self._layout_kpi_grid(cols)

    def _layout_kpi_grid(self, cols: int):
        self._kpi_cols_mode = cols
        for card in self.kpi_cards:
            card.grid_forget()
        for c in range(4):
            self.stats_row.columnconfigure(c, weight=1 if c < cols else 0, uniform="kpi" if c < cols else "")

        for idx, card in enumerate(self.kpi_cards):
            r = idx // cols
            c = idx % cols
            px = (0, 5) if c == 0 and cols > 1 else ((5, 0) if c == cols - 1 and cols > 1 else (5 if cols > 1 else 0))
            py = (0, 6) if r < (len(self.kpi_cards) - 1) // cols else 0
            card.grid(row=r, column=c, sticky="nsew", padx=px, pady=py)

    def run_manual_health_check(self):
        def _job():
            state = perform_health_check(auto_recover_funnel=True, source="manual_panel_check")
            self.root.after(0, self.refresh_ui_status)
            self.root.after(
                0,
                lambda: messagebox.showinfo(
                    "Chequeo de Salud Completado",
                    f"Estado del Puente: {state.get('status')}\n"
                    f"Versión: v{state.get('bridge_version')}\n"
                    f"Puerto Local Activo: :{state.get('local_port')}\n"
                    f"Funnel :8443 Activo: {'Sí' if state.get('funnel_8443_active') else 'No'}\n"
                    f"CPU: {state.get('cpu_percent')}% · RAM: {state.get('memory_percent')}%\n"
                    f"Hora del chequeo: {state.get('last_check_local')}"
                )
            )
        threading.Thread(target=_job, daemon=True).start()

    # =========================================================================
    # SECCIÓN PUERTOS (RESPONSIVE SPLIT + FLOW TOOLBAR + HORIZONTAL SCROLL)
    # =========================================================================
    def build_ports_section(self, parent: tk.Frame):
        top_grid = tk.Frame(parent, bg=self.c_bg)
        top_grid.pack(fill="x", pady=(0, 12))

        p_local_card = RoundedCard(
            top_grid, bg_outer=self.c_bg, bg_card=self.c_surface,
            border_color=self.c_border, hover_border=self.c_border_hover, shadow_color=self.c_shadow, padx=16, pady=12
        )
        tk.Label(
            p_local_card.body,
            text="PUERTO LOCAL DEL SERVIDOR PUENTE",
            font=self.f_sans_xs,
            bg=self.c_surface,
            fg=self.c_text3,
            anchor="w",
            justify="left"
        ).pack(anchor="w", fill="x")

        self.lbl_port_current_val = tk.Label(
            p_local_card.body,
            text=f"127.0.0.1:{self.local_port}",
            font=self.f_kpi_val,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        )
        self.lbl_port_current_val.pack(anchor="w", fill="x", pady=(3, 1))

        self.lbl_port_current_owner = tk.Label(
            p_local_card.body,
            text="Verificando estado del puerto...",
            font=self.f_sans_sm,
            bg=self.c_surface,
            fg=self.c_text2,
            anchor="w",
            justify="left"
        )
        self.lbl_port_current_owner.pack(anchor="w", fill="x")

        p_funnel_card = RoundedCard(
            top_grid, bg_outer=self.c_bg, bg_card=self.c_surface,
            border_color=self.c_border, hover_border=self.c_border_hover, shadow_color=self.c_shadow, padx=16, pady=12
        )
        tk.Label(
            p_funnel_card.body,
            text="PUERTO PÚBLICO TAILSCALE FUNNEL (FIJO)",
            font=self.f_sans_xs,
            bg=self.c_surface,
            fg=self.c_text3,
            anchor="w",
            justify="left"
        ).pack(anchor="w", fill="x")

        self.lbl_port_funnel_val = tk.Label(
            p_funnel_card.body,
            text=f"HTTPS :{FUNNEL_PORT}  ➔  Local :{self.local_port}",
            font=self.f_kpi_val,
            bg=self.c_surface,
            fg=self.c_green,
            anchor="w",
            justify="left"
        )
        self.lbl_port_funnel_val.pack(anchor="w", fill="x", pady=(3, 1))

        self.lbl_port_funnel_owner = tk.Label(
            p_funnel_card.body,
            text=f"Túnel HTTPS público (:{FUNNEL_PORT}) redirigiendo al puerto local activo",
            font=self.f_sans_sm,
            bg=self.c_surface,
            fg=self.c_text2,
            anchor="w",
            justify="left"
        )
        self.lbl_port_funnel_owner.pack(anchor="w", fill="x")

        self._bind_responsive_split(top_grid, p_local_card, p_funnel_card, breakpoint=820)

        main_card = tk.Frame(parent, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=18, pady=14)
        main_card.pack(fill="both", expand=True)

        hdr = tk.Frame(main_card, bg=self.c_surface)
        hdr.pack(fill="x", pady=(0, 8))

        tk.Label(
            hdr,
            text="Explorador y Selector de Puertos Locales",
            font=self.f_title_md,
            bg=self.c_surface,
            fg=self.c_text
        ).pack(side="left")

        # FlowFrame para controles de escaneo y aplicación de puerto
        ports_flow = FlowFrame(main_card, bg=self.c_bg, gap_x=10, gap_y=8)
        ports_flow.pack(fill="x", pady=(0, 10))

        range_grp = tk.Frame(ports_flow, bg=self.c_bg)
        tk.Label(range_grp, text="Rango:", font=self.f_sans_bold, bg=self.c_bg, fg=self.c_text).pack(side="left")
        tk.Entry(range_grp, textvariable=self.var_scan_start, width=6, font=self.f_mono, bg=self.c_surface, fg=self.c_text, insertbackground=self.c_text, relief="flat", highlightbackground=self.c_border, highlightthickness=1).pack(side="left", padx=4, ipady=3)
        tk.Label(range_grp, text="–", font=self.f_sans, bg=self.c_bg, fg=self.c_text2).pack(side="left")
        tk.Entry(range_grp, textvariable=self.var_scan_end, width=6, font=self.f_mono, bg=self.c_surface, fg=self.c_text, insertbackground=self.c_text, relief="flat", highlightbackground=self.c_border, highlightthickness=1).pack(side="left", padx=4, ipady=3)
        ports_flow.add_item(range_grp)

        self.btn_scan_ports = self.make_button(
            ports_flow,
            text="⌕  Buscar puertos disponibles",
            command=self.run_port_range_scan,
            variant="primary"
        )
        ports_flow.add_item(self.btn_scan_ports)

        apply_grp = tk.Frame(ports_flow, bg=self.c_bg)
        tk.Label(apply_grp, text="Puerto elegido:", font=self.f_sans_bold, bg=self.c_bg, fg=self.c_text).pack(side="left")
        self.entry_new_port = tk.Entry(
            apply_grp,
            textvariable=self.var_selected_port,
            width=7,
            font=("Consolas", 10, "bold"),
            bg=self.c_surface,
            fg=self.c_text,
            insertbackground=self.c_text,
            relief="flat",
            highlightbackground=self.c_navy,
            highlightthickness=1
        )
        self.entry_new_port.pack(side="left", padx=6, ipady=3)
        ports_flow.add_item(apply_grp)

        btn_apply_p = self.make_button(
            ports_flow,
            text="✓  Aplicar Puerto y Guardar",
            command=self.apply_selected_port,
            variant="success"
        )
        ports_flow.add_item(btn_apply_p)

        table_wrap = tk.Frame(main_card, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1)
        table_wrap.pack(fill="both", expand=True)
        table_wrap.rowconfigure(0, weight=1)
        table_wrap.columnconfigure(0, weight=1)

        p_cols = ("port", "status", "process", "recommendation")
        self.ports_tree = ttk.Treeview(
            table_wrap,
            columns=p_cols,
            show="headings",
            style="Audit.Treeview",
            height=8
        )
        self.ports_tree.heading("port", text="Puerto Local")
        self.ports_tree.heading("status", text="Estado")
        self.ports_tree.heading("process", text="Proceso / PID Detectado")
        self.ports_tree.heading("recommendation", text="Disponibilidad")

        self.ports_tree.column("port", width=110, minwidth=90, anchor="center", stretch=False)
        self.ports_tree.column("status", width=140, minwidth=120, anchor="center", stretch=False)
        self.ports_tree.column("process", width=340, minwidth=240, anchor="w", stretch=True)
        self.ports_tree.column("recommendation", width=300, minwidth=220, anchor="w", stretch=True)

        self.ports_tree.tag_configure("FREE", foreground=self.c_green)
        self.ports_tree.tag_configure("OWN", foreground="#60A5FA" if self.theme_mode == "oscuro" else self.c_navy)
        self.ports_tree.tag_configure("BUSY", foreground=self.c_red)

        p_yscroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.ports_tree.yview, style="Vertical.TScrollbar")
        p_xscroll = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.ports_tree.xview, style="Horizontal.TScrollbar")
        self.ports_tree.configure(yscrollcommand=p_yscroll.set, xscrollcommand=p_xscroll.set)

        self.ports_tree.grid(row=0, column=0, sticky="nsew")
        p_yscroll.grid(row=0, column=1, sticky="ns")
        p_xscroll.grid(row=1, column=0, sticky="ew")

        self.ports_tree.bind("<<TreeviewSelect>>", self._on_port_row_selected)
        self.attach_treeview_tooltip(self.ports_tree)

        self.root.after(200, self.run_port_range_scan)

    def _on_port_row_selected(self, _event=None):
        sel = self.ports_tree.selection()
        if not sel:
            return
        vals = self.ports_tree.item(sel[0], "values")
        if vals:
            p_str = str(vals[0]).lstrip(":")
            self.var_selected_port.set(p_str)

    def refresh_ports_section_status(self):
        self.local_port = get_configured_local_port()
        info = inspect_port(self.local_port, check_http_bridge=True)
        fnl_ok = self.is_funnel_8443_active()

        if hasattr(self, "lbl_port_current_val"):
            self.lbl_port_current_val.config(text=f"127.0.0.1:{self.local_port}")
            if info["in_use"] and info["is_own_bridge"]:
                self.lbl_port_current_owner.config(
                    text=f"● Ocupado por MuseBridge ({info['owner_description']})",
                    fg=self.c_green
                )
            elif info["in_use"] and not info["is_own_bridge"]:
                self.lbl_port_current_owner.config(
                    text=f"✕ OCUPADO POR OTRO PROCESO: {info['owner_description']} (Error 10048)",
                    fg=self.c_red
                )
            else:
                self.lbl_port_current_owner.config(
                    text="○ Libre (Servidor MuseBridge detenido)",
                    fg=self.c_amber
                )

        if hasattr(self, "lbl_port_funnel_val"):
            fnl_state = "ACTIVO" if fnl_ok else "INACTIVO"
            self.lbl_port_funnel_val.config(
                text=f"HTTPS :{FUNNEL_PORT}  ➔  Local :{self.local_port} ({fnl_state})",
                fg=self.c_green if fnl_ok else self.c_amber
            )
            self.lbl_port_funnel_owner.config(
                text=f"Túnel público :{FUNNEL_PORT} redirigido a 127.0.0.1:{self.local_port}"
            )

    def run_port_range_scan(self):
        try:
            start_p = int(self.var_scan_start.get().strip())
            end_p = int(self.var_scan_end.get().strip())
        except ValueError:
            messagebox.showwarning("Rango Inválido", "Ingresa números de puerto válidos para el rango (ej. 8000 a 8010).")
            return

        if hasattr(self, "btn_scan_ports"):
            self.btn_scan_ports.config(text="⟳ Escaneando...", state="disabled")
            self.root.update_idletasks()

        def _scan_job():
            results = scan_port_range(start_p, end_p)
            try:
                self.root.after(0, lambda: self._populate_ports_tree(results))
            except Exception:
                pass

        threading.Thread(target=_scan_job, daemon=True).start()

    def _populate_ports_tree(self, results: List[Dict[str, Any]]):
        if hasattr(self, "btn_scan_ports"):
            self.btn_scan_ports.config(text="⌕  Buscar puertos disponibles", state="normal")
        self.refresh_ports_section_status()

        if not hasattr(self, "ports_tree"):
            return
        for item_id in self.ports_tree.get_children():
            self.ports_tree.delete(item_id)

        for info in results:
            p_num = info["port"]
            if not info["in_use"]:
                st_txt = "● LIBRE"
                proc_txt = "Ninguno"
                rec_txt = "✓ Disponible para elegir y aplicar"
                tag = "FREE"
            elif info["is_own_bridge"]:
                st_txt = "● MUSEBRIDGE"
                proc_txt = info["owner_description"]
                rec_txt = "Puerto actual en uso por este puente"
                tag = "OWN"
            else:
                st_txt = "✕ OCUPADO"
                proc_txt = info["owner_description"]
                rec_txt = "No disponible (Ocupado por otro proceso)"
                tag = "BUSY"

            self.ports_tree.insert("", tk.END, values=(f":{p_num}", st_txt, proc_txt, rec_txt), tags=(tag,))

    def apply_selected_port(self):
        raw_val = self.var_selected_port.get().strip().lstrip(":")
        try:
            new_port = int(raw_val)
        except ValueError:
            messagebox.showerror("Puerto Inválido", f"'{raw_val}' no es un número de puerto válido.")
            return

        ok, msg, _info = validate_port_selection(new_port)
        if not ok:
            messagebox.showerror("Puerto Ocupado o Inválido", msg)
            return

        old_port = self.local_port
        old_info = inspect_port(old_port, check_http_bridge=True)

        try:
            save_configured_local_port(new_port)
        except Exception as exc:
            messagebox.showerror("Error al Guardar", f"No se pudo guardar el puerto en la configuración:\n{exc}")
            return

        self.local_port = new_port
        self.permissions = load_permissions()
        self.update_dynamic_port_labels()

        if old_info["in_use"] and old_info["is_own_bridge"] and old_info["pid"] and psutil:
            try:
                p = psutil.Process(old_info["pid"])
                for child in p.children(recursive=True):
                    child.terminate()
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                pass
            time.sleep(0.5)

        self.start_server()
        self.run_port_range_scan()
        self.refresh_ui_status()

        messagebox.showinfo(
            "Puerto Aplicado y Guardado",
            f"¡El puente ahora opera en el puerto local :{new_port}!\n\n"
            f"• Configuración guardada en permissions.json y .env (se usará en todos los arranques siguientes).\n"
            f"• Servidor local activo en: http://127.0.0.1:{new_port}\n"
            f"• Tailscale Funnel (:{FUNNEL_PORT}) redirigido automáticamente a 127.0.0.1:{new_port}."
        )

    def update_dynamic_port_labels(self):
        if hasattr(self, "lbl_sidebar_footer"):
            if self.sidebar_collapsed:
                self.lbl_sidebar_footer.config(text=f":{self.local_port}\nv{BRIDGE_VERSION}")
            else:
                self.lbl_sidebar_footer.config(text=f"Local :{self.local_port} · Funnel :{FUNNEL_PORT}\nMuseBridge v{BRIDGE_VERSION}")
        if hasattr(self, "lbl_kpi_funnel_sub"):
            self.lbl_kpi_funnel_sub.config(text=f"Proxy HTTPS -> :{self.local_port}")
        self.refresh_ports_section_status()

    # =========================================================================
    # SECCIÓN 2: ALTA POR CÓDIGO QR (RESPONSIVE SPLIT 2-COL / 1-COL)
    # =========================================================================
    def build_connection_section(self, parent: tk.Frame):
        grid = tk.Frame(parent, bg=self.c_bg)
        grid.pack(fill="both", expand=True)

        left_card = tk.Frame(grid, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=18, pady=14)
        tk.Label(
            left_card,
            text="Alta Instantánea por Código QR",
            font=self.f_title_md,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        ).pack(anchor="w", fill="x")

        lbl_qr_desc = tk.Label(
            left_card,
            text="Envía una foto o captura de este QR a tu Muse AI. Muse leerá la URL y el token, validará el handshake y confirmará la conexión SIN mostrar nunca tu clave en el chat.",
            font=self.f_sans_sm,
            bg=self.c_surface,
            fg=self.c_text2,
            wraplength=520,
            anchor="w",
            justify="left"
        )
        lbl_qr_desc.pack(anchor="w", fill="x", pady=(4, 8))
        left_card.bind("<Configure>", lambda e: lbl_qr_desc.config(wraplength=max(240, e.width - 44)))

        qr_box = tk.Frame(left_card, bg=self.c_surface2, padx=8, pady=8, highlightbackground=self.c_border, highlightthickness=1)
        qr_box.pack(pady=2)

        self.lbl_qr_image = tk.Label(qr_box, bg="#FFFFFF", padx=4, pady=4)
        self.lbl_qr_image.pack()

        self.lbl_qr_countdown = tk.Label(
            left_card,
            text="Validez del QR: 15:00 min",
            font=self.f_sans_bold,
            bg=self.c_green_light,
            fg=self.c_green,
            padx=12,
            pady=3
        )
        self.lbl_qr_countdown.pack(pady=6)

        qr_btns = FlowFrame(left_card, bg=self.c_surface, gap_x=8, gap_y=6)
        qr_btns.pack(fill="x", pady=2)
        qr_btns.add_item(self.make_button(qr_btns, text="⟳  Regenerar QR Nuevo", command=self.regenerate_qr, variant="secondary"))
        qr_btns.add_item(self.make_button(qr_btns, text="Guardar Imagen QR (.png)", command=self.save_qr_image, variant="primary"))

        right_card = tk.Frame(grid, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=18, pady=14)

        tk.Label(
            right_card,
            text="Flujo Paso a Paso (Si el QR Falla)",
            font=self.f_title_md,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        ).pack(anchor="w", fill="x")

        lbl_man_desc = tk.Label(
            right_card,
            text="Si Muse no puede leer la imagen del QR, sigue este flujo seguro de 2 pasos (primero la URL y luego la clave):",
            font=self.f_sans_sm,
            bg=self.c_surface,
            fg=self.c_text2,
            wraplength=520,
            anchor="w",
            justify="left"
        )
        lbl_man_desc.pack(anchor="w", fill="x", pady=(4, 10))
        right_card.bind("<Configure>", lambda e: lbl_man_desc.config(wraplength=max(240, e.width - 44)))

        step1 = tk.Frame(right_card, bg=self.c_bg, highlightbackground=self.c_border, highlightthickness=1, padx=12, pady=10)
        step1.pack(fill="x", pady=(0, 10))

        tk.Label(
            step1,
            text="PASO 1 · DIRECCIÓN PÚBLICA DEL FUNNEL (:8443)",
            font=self.f_sans_xs,
            bg=self.c_bg,
            fg="#60A5FA" if self.theme_mode == "oscuro" else self.c_navy,
            anchor="w",
            justify="left"
        ).pack(anchor="w", fill="x")

        s1_row = tk.Frame(step1, bg=self.c_bg)
        s1_row.pack(fill="x", pady=(5, 0))

        # Empaquetar botón a la derecha PRIMERO para que jamás se corte
        self.make_button(s1_row, text="Copiar URL", command=self.copy_funnel_url, variant="primary", padx=10, pady=4).pack(side="right")

        self.txt_funnel_url = tk.Entry(
            s1_row,
            font=self.f_mono,
            bg=self.c_surface,
            fg=self.c_text,
            readonlybackground=self.c_surface,
            relief="flat",
            highlightbackground=self.c_border,
            highlightthickness=1,
            width=16
        )
        self.txt_funnel_url.insert(0, self.get_display_funnel_url())
        self.txt_funnel_url.configure(state="readonly")
        self.txt_funnel_url.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))

        step2 = tk.Frame(right_card, bg=self.c_bg, highlightbackground=self.c_border, highlightthickness=1, padx=12, pady=10)
        step2.pack(fill="x", pady=(0, 10))

        tk.Label(
            step2,
            text="PASO 2 · CLAVE SECRETA (BEARER TOKEN)",
            font=self.f_sans_xs,
            bg=self.c_bg,
            fg=self.c_green,
            anchor="w",
            justify="left"
        ).pack(anchor="w", fill="x")

        s2_row = tk.Frame(step2, bg=self.c_bg)
        s2_row.pack(fill="x", pady=(5, 0))

        # Empaquetar botones a la derecha PRIMERO
        self.make_button(s2_row, text="Copiar Clave", command=self.copy_token, variant="success", padx=10, pady=4).pack(side="right")

        self.btn_toggle_mask = self.make_button(
            s2_row, text="Ver", command=self.toggle_token_visibility, variant="secondary", padx=8, pady=4, font=self.f_sans_sm
        )
        self.btn_toggle_mask.pack(side="right", padx=(0, 6))

        self.txt_token_display = tk.Entry(
            s2_row,
            font=self.f_mono,
            bg=self.c_surface,
            fg=self.c_text,
            readonlybackground=self.c_surface,
            show="•",
            relief="flat",
            highlightbackground=self.c_border,
            highlightthickness=1,
            width=14
        )
        self.txt_token_display.insert(0, self.api_token)
        self.txt_token_display.configure(state="readonly")
        self.txt_token_display.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))

        act_btns = FlowFrame(right_card, bg=self.c_surface, gap_x=8, gap_y=8)
        act_btns.pack(fill="x", pady=(4, 0))
        act_btns.add_item(
            self.make_button(
                act_btns,
                text="Rotar Token Secreto (.env)",
                command=self.regenerate_token,
                variant="secondary",
                padx=12,
                pady=6,
                font=self.f_sans_sm
            )
        )
        act_btns.add_item(
            self.make_button(
                act_btns,
                text="Copiar Instrucción para Muse AI",
                command=self.copy_all_for_muse,
                variant="primary",
                padx=14,
                pady=6
            )
        )

        self._bind_responsive_split(grid, left_card, right_card, breakpoint=820)
        self.render_qr()

    # =========================================================================
    # SECCIÓN 3: EXPLORADOR DE APPS INSTALADAS Y PERMISOS GENERALES
    # =========================================================================
    def build_apps_section(self, parent: tk.Frame):
        caps_card = tk.Frame(parent, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=18, pady=12)
        caps_card.pack(fill="x", pady=(0, 12))

        top_caps = tk.Frame(caps_card, bg=self.c_surface)
        top_caps.pack(fill="x", pady=(0, 8))

        tk.Label(
            top_caps,
            text="1. Interruptores de Permisos Generales (Efecto Inmediato)",
            font=self.f_title_md,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        ).pack(anchor="w")

        self.lbl_perms_saved = tk.Label(
            top_caps,
            text="✓ Sincronizado con permissions.json",
            font=("Segoe UI", 8),
            bg=self.c_green_light,
            fg=self.c_green,
            padx=9,
            pady=2,
            anchor="w",
            justify="left"
        )
        self.lbl_perms_saved.pack(anchor="w", pady=(4, 0))

        caps_flow = FlowFrame(caps_card, bg=self.c_surface, gap_x=16, gap_y=6)
        caps_flow.pack(fill="x")

        self.var_allow_screenshot = tk.BooleanVar(value=self.permissions.get("allow_screenshot", False))
        self.var_allow_whatsapp = tk.BooleanVar(value=self.permissions.get("allow_whatsapp", False))
        self.var_allow_gui = tk.BooleanVar(value=self.permissions.get("allow_gui_control", False))
        self.var_allow_open_app = tk.BooleanVar(value=self.permissions.get("allow_open_app", False))

        chk_defs = [
            (self.var_allow_screenshot, "Captura de Pantalla (/pc/screenshot)"),
            (self.var_allow_whatsapp, "Enviar WhatsApp (/whatsapp/send)"),
            (self.var_allow_gui, "Mouse y Teclado (/pc/type-and-click)"),
            (self.var_allow_open_app, "Abrir Aplicaciones (/pc/open-app)"),
        ]
        for var, txt in chk_defs:
            cb = tk.Checkbutton(
                caps_flow,
                text=txt,
                variable=var,
                command=self.save_all_permissions,
                bg=self.c_surface,
                fg=self.c_text,
                selectcolor=self.c_surface2,
                activebackground=self.c_surface,
                activeforeground=self.c_text,
                font=self.f_sans_bold,
                cursor="hand2"
            )
            caps_flow.add_item(cb)

        apps_card = tk.Frame(parent, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=18, pady=14)
        apps_card.pack(fill="both", expand=True)

        hdr_row = tk.Frame(apps_card, bg=self.c_surface)
        hdr_row.pack(fill="x", pady=(0, 8))

        self.lbl_apps_summary = tk.Label(
            hdr_row,
            text="0 detectadas · 0 autorizadas",
            font=("Segoe UI Semibold", 8),
            bg=self.c_navy_light,
            fg="#60A5FA" if self.theme_mode == "oscuro" else self.c_navy,
            padx=10,
            pady=3
        )
        self.lbl_apps_summary.pack(side="right")

        tk.Label(
            hdr_row,
            text="2. Explorador de Aplicaciones Instaladas",
            font=self.f_title_md,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        ).pack(side="left")

        search_flow = FlowFrame(apps_card, bg=self.c_surface, gap_x=6, gap_y=6)
        search_flow.pack(fill="x", pady=(0, 10))

        search_box = tk.Frame(search_flow, bg=self.c_surface2, highlightbackground=self.c_border, highlightthickness=1, padx=10, pady=4)
        tk.Label(search_box, text="⌕ Buscar:", bg=self.c_surface2, fg=self.c_text2, font=self.f_sans_sm).pack(side="left", padx=(0, 6))
        self.entry_app_search = tk.Entry(
            search_box,
            textvariable=self.app_search_var,
            bg=self.c_surface2,
            fg=self.c_text,
            insertbackground=self.c_text,
            font=self.f_sans,
            relief="flat",
            bd=0,
            width=24
        )
        self.entry_app_search.pack(side="left")
        search_flow.add_item(search_box)

        self.btn_flt_all = self.make_button(search_flow, text="Todas", command=lambda: self.set_app_filter("all"), variant="primary", padx=11, pady=5)
        search_flow.add_item(self.btn_flt_all)

        self.btn_flt_allowed = self.make_button(search_flow, text="Permitidas", command=lambda: self.set_app_filter("allowed"), variant="secondary", padx=11, pady=5)
        search_flow.add_item(self.btn_flt_allowed)

        self.btn_flt_denied = self.make_button(search_flow, text="Denegadas", command=lambda: self.set_app_filter("denied"), variant="secondary", padx=11, pady=5)
        search_flow.add_item(self.btn_flt_denied)

        self.btn_rescan_apps = self.make_button(search_flow, text="⟳ Actualizar lista", command=self.rescan_pc_apps, variant="accent_subtle", padx=12, pady=5)
        search_flow.add_item(self.btn_rescan_apps)

        btn_deny_all = self.make_button(search_flow, text="✕ Denegar Todas", command=self.deny_all_apps, variant="danger_subtle", padx=11, pady=5)
        search_flow.add_item(btn_deny_all)

        list_wrap = tk.Frame(apps_card, bg=self.c_bg, highlightbackground=self.c_border, highlightthickness=1)
        list_wrap.pack(fill="both", expand=True)

        self.apps_canvas = tk.Canvas(list_wrap, bg=self.c_surface, highlightthickness=0, height=270)
        apps_scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.apps_canvas.yview, style="Vertical.TScrollbar")
        self.apps_list_inner = tk.Frame(self.apps_canvas, bg=self.c_surface)

        self.apps_list_win = self.apps_canvas.create_window((0, 0), window=self.apps_list_inner, anchor="nw")
        self.apps_canvas.configure(yscrollcommand=apps_scroll.set)

        self.apps_canvas.pack(side="left", fill="both", expand=True)
        apps_scroll.pack(side="right", fill="y")

        self.apps_list_inner.bind("<Configure>", lambda _e: self.apps_canvas.configure(scrollregion=self.apps_canvas.bbox("all")))
        self.apps_canvas.bind("<Configure>", lambda e: self.apps_canvas.itemconfigure(self.apps_list_win, width=e.width))

        self.apps_canvas.bind("<Enter>", lambda _e: self.apps_canvas.bind_all("<MouseWheel>", self._on_apps_mousewheel))
        self.apps_canvas.bind("<Leave>", lambda _e: self.apps_canvas.unbind_all("<MouseWheel>"))

        self.app_check_vars: Dict[str, tk.BooleanVar] = {}
        self.populate_apps_list()

    def _on_apps_mousewheel(self, event):
        self.apps_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def set_app_filter(self, mode: str):
        self.app_filter_mode = mode
        for btn, m in ((self.btn_flt_all, "all"), (self.btn_flt_allowed, "allowed"), (self.btn_flt_denied, "denied")):
            if m == mode:
                btn._bg_norm = self.c_navy
                btn._bg_hov = self.c_navy_hover
                btn.config(bg=self.c_navy, fg="#FFFFFF", font=self.f_sans_bold)
            else:
                btn._bg_norm = self.c_surface2
                btn._bg_hov = self.c_surface2_hover
                btn.config(bg=self.c_surface2, fg=self.c_text, font=self.f_sans)
        self.populate_apps_list()

    def rescan_pc_apps(self):
        self.btn_rescan_apps.config(text="⟳ Escaneando...", state="disabled")
        self.root.update_idletasks()
        scanned = scan_installed_apps()
        self.permissions = sync_scanned_apps(scanned)
        self.btn_rescan_apps.config(text="⟳ Actualizar lista", state="normal")
        self.populate_apps_list()
        self.refresh_kpi_counters()
        messagebox.showinfo("Lista Actualizada", f"Escaneo completado: {len(scanned)} aplicaciones detectadas en tu PC.")

    def deny_all_apps(self):
        if messagebox.askyesno("Denegar Todas", "¿Deseas revocar el permiso de apertura a todas las aplicaciones?"):
            allowed_map = self.permissions.get("allowed_apps", {})
            for k in allowed_map:
                allowed_map[k] = False
            for var in self.app_check_vars.values():
                var.set(False)
            self.permissions["allowed_apps"] = allowed_map
            save_permissions(self.permissions)
            self.populate_apps_list()
            self.refresh_kpi_counters()

    def toggle_app_switch(self, app_id: str):
        allowed_map = self.permissions.get("allowed_apps", {})
        new_val = not bool(allowed_map.get(app_id, False))
        allowed_map[app_id] = new_val
        self.permissions["allowed_apps"] = allowed_map
        if app_id in self.app_check_vars:
            self.app_check_vars[app_id].set(new_val)
        save_permissions(self.permissions)
        self.populate_apps_list()
        self.refresh_kpi_counters()

    def on_toggle_single_app(self, app_id: str):
        var = self.app_check_vars.get(app_id)
        if var is None:
            return
        if "allowed_apps" not in self.permissions:
            self.permissions["allowed_apps"] = {}
        self.permissions["allowed_apps"][app_id] = bool(var.get())
        save_permissions(self.permissions)
        self.populate_apps_list()
        self.refresh_kpi_counters()

    def update_apps_summary_label(self):
        catalog = self.permissions.get("installed_apps_catalog", {})
        allowed_map = self.permissions.get("allowed_apps", {})
        total = len(catalog)
        auth_count = sum(1 for k in catalog if allowed_map.get(k, False))
        self.lbl_apps_summary.config(text=f"{total} instaladas · {auth_count} permitidas")

    def populate_apps_list(self):
        for child in self.apps_list_inner.winfo_children():
            child.destroy()

        catalog: Dict[str, Dict[str, Any]] = self.permissions.get("installed_apps_catalog", {})
        allowed_map: Dict[str, bool] = self.permissions.get("allowed_apps", {})
        query = self.app_search_var.get().strip().lower()

        items = sorted(catalog.values(), key=lambda x: (not allowed_map.get(x["id"], False), x.get("name", "").lower()))

        shown = 0
        for item in items:
            app_id = item["id"]
            name = item.get("name", app_id)
            path = item.get("path", "")
            source = item.get("source", "Sistema")
            is_allowed = bool(allowed_map.get(app_id, False))

            if self.app_filter_mode == "allowed" and not is_allowed:
                continue
            if self.app_filter_mode == "denied" and is_allowed:
                continue
            if query and (query not in name.lower() and query not in app_id.lower() and query not in path.lower() and query not in source.lower()):
                continue

            if app_id not in self.app_check_vars:
                self.app_check_vars[app_id] = tk.BooleanVar(value=is_allowed)
            else:
                self.app_check_vars[app_id].set(is_allowed)

            row_bg = self.c_row_ok if is_allowed else (self.c_surface if shown % 2 == 0 else self.c_row_alt)
            row = tk.Frame(self.apps_list_inner, bg=row_bg, padx=12, pady=6)
            row.pack(fill="x")
            tk.Frame(self.apps_list_inner, bg=self.c_border, height=1).pack(fill="x")

            disp_name = name if len(name) <= 38 else (name[:35] + "…")
            cb = tk.Checkbutton(
                row,
                text=disp_name,
                variable=self.app_check_vars[app_id],
                command=lambda aid=app_id: self.on_toggle_single_app(aid),
                bg=row_bg,
                fg=self.c_text,
                selectcolor=self.c_surface2,
                activebackground=row_bg,
                activeforeground=self.c_text,
                font=self.f_sans_bold,
                anchor="w",
                cursor="hand2"
            )
            cb.pack(side="left")
            Tooltip(cb, f"{name}\nRuta: {path}\nOrigen: {source}", bg=self.c_tip_bg)

            sw_txt = "● PERMITIDA" if is_allowed else "○ DENEGADA"
            sw_bg = self.c_green if is_allowed else self.c_surface2
            sw_fg = "#FFFFFF" if is_allowed else self.c_text2
            tk.Button(
                row,
                text=sw_txt,
                command=lambda aid=app_id: self.toggle_app_switch(aid),
                font=("Segoe UI Semibold", 8),
                bg=sw_bg,
                fg=sw_fg,
                relief="flat",
                padx=10,
                pady=2,
                cursor="hand2"
            ).pack(side="right", padx=(6, 0))

            tk.Label(
                row,
                text=source,
                font=("Segoe UI", 8),
                bg=self.c_navy_light,
                fg="#60A5FA" if self.theme_mode == "oscuro" else self.c_navy,
                padx=7,
                pady=2
            ).pack(side="right", padx=4)

            short_path = path if len(path) <= 44 else ("…" + path[-41:])
            lbl_p = tk.Label(
                row,
                text=short_path,
                font=("Consolas", 8),
                bg=row_bg,
                fg=self.c_text3
            )
            lbl_p.pack(side="right", padx=6)
            Tooltip(lbl_p, path, bg=self.c_tip_bg)

            shown += 1

        if shown == 0:
            tk.Label(
                self.apps_list_inner,
                text="No se encontraron aplicaciones con ese criterio de búsqueda.",
                font=self.f_sans,
                bg=self.c_surface,
                fg=self.c_text3,
                pady=24
            ).pack()

        self.update_apps_summary_label()

    # =========================================================================
    # SECCIÓN 4: FUNCIONES DE ARCHIVOS Y CARPETAS AUTORIZADAS (RESPONSIVE SPLIT)
    # =========================================================================
    def build_files_section(self, parent: tk.Frame):
        grid = tk.Frame(parent, bg=self.c_bg)
        grid.pack(fill="both", expand=True)

        left_card = tk.Frame(grid, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=16, pady=12)

        tk.Label(
            left_card,
            text="Permisos Individuales de Archivos (8 Funciones)",
            font=self.f_title_md,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        ).pack(anchor="w", fill="x")

        lbl_f_sub = tk.Label(
            left_card,
            text="Todos en false por defecto. Cada edición crea respaldo .bak automático y verifica el hash SHA-256.",
            font=self.f_sans_sm,
            bg=self.c_surface,
            fg=self.c_text2,
            wraplength=520,
            anchor="w",
            justify="left"
        )
        lbl_f_sub.pack(anchor="w", fill="x", pady=(2, 8))
        left_card.bind("<Configure>", lambda e: lbl_f_sub.config(wraplength=max(240, e.width - 40)))

        self.var_file_list = tk.BooleanVar(value=self.permissions.get("allow_file_list", False))
        self.var_file_read = tk.BooleanVar(value=self.permissions.get("allow_file_read", False))
        self.var_file_write = tk.BooleanVar(value=self.permissions.get("allow_file_write", False))
        self.var_file_mkdir = tk.BooleanVar(value=self.permissions.get("allow_file_mkdir", False))
        self.var_file_move = tk.BooleanVar(value=self.permissions.get("allow_file_move", False))
        self.var_file_search = tk.BooleanVar(value=self.permissions.get("allow_file_search", False))
        self.var_file_transfer = tk.BooleanVar(value=self.permissions.get("allow_file_transfer", False))
        self.var_allow_delete = tk.BooleanVar(value=self.permissions.get("allow_delete", False))

        file_perm_defs = [
            (self.var_file_list, "Listar carpetas y archivos (/files/list)", "Explorar nombres y tamaños dentro de rutas autorizadas."),
            (self.var_file_read, "Leer archivos de texto (/files/read)", "Lectura verificada con hash SHA-256."),
            (self.var_file_write, "Crear y editar con respaldo .bak (/files/write y /files/edit)", "Crea .bak obligatorio y verifica cambios reales en disco."),
            (self.var_file_mkdir, "Crear carpetas (/files/mkdir)", "Creación verificada dentro de rutas autorizadas."),
            (self.var_file_move, "Mover y renombrar (/files/move)", "Origen y destino validados dentro del sandbox."),
            (self.var_file_search, "Buscar por nombre y contenido (/files/search)", "Busca coincidencias en nombres y dentro del texto."),
            (self.var_file_transfer, "Subir y bajar archivos (/files/upload y /files/download)", "Transferencia Base64 con respaldo .bak."),
        ]

        for var, title, desc in file_perm_defs:
            box = tk.Frame(left_card, bg=self.c_bg, padx=8, pady=3, highlightbackground=self.c_border, highlightthickness=1)
            box.pack(fill="x", pady=2)
            cb = tk.Checkbutton(
                box,
                text=title,
                variable=var,
                command=self.save_all_permissions,
                bg=self.c_bg,
                fg=self.c_text,
                selectcolor=self.c_surface2,
                activebackground=self.c_bg,
                activeforeground=self.c_text,
                font=("Segoe UI Semibold", 9),
                anchor="w",
                justify="left",
                cursor="hand2"
            )
            cb.pack(anchor="w", fill="x")
            Tooltip(cb, desc, bg=self.c_tip_bg)

        del_box = tk.Frame(left_card, bg=self.c_red_light, padx=8, pady=4, highlightbackground=self.c_red, highlightthickness=1)
        del_box.pack(fill="x", pady=(5, 5))

        cb_del = tk.Checkbutton(
            del_box,
            text="Borrar SOLO a Papelera de Reciclaje (allow_delete aparte)",
            variable=self.var_allow_delete,
            command=self.save_all_permissions,
            bg=self.c_red_light,
            fg=self.c_red,
            selectcolor=self.c_surface,
            activebackground=self.c_red_light,
            font=("Segoe UI Semibold", 9),
            anchor="w",
            justify="left",
            cursor="hand2"
        )
        cb_del.pack(anchor="w", fill="x")
        Tooltip(cb_del, "Jamás ejecuta borrado permanente. Envía a la Papelera de Windows (SHFileOperationW).", bg=self.c_tip_bg)

        self.make_button(
            left_card,
            text="✕  Desactivar Todos los Permisos de Archivos",
            command=self.disable_all_file_permissions,
            variant="secondary",
            padx=10,
            pady=4,
            font=self.f_sans_sm
        ).pack(anchor="w", pady=(2, 0))

        right_card = tk.Frame(grid, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=16, pady=12)

        tk.Label(
            right_card,
            text="Rutas Autorizadas por el Usuario (Sandbox)",
            font=self.f_title_md,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        ).pack(anchor="w", fill="x")

        lbl_r_sub = tk.Label(
            right_card,
            text="Las funciones de archivos SOLO operan dentro de las carpetas listadas aquí. Carpetas del sistema están bloqueadas siempre.",
            font=self.f_sans_sm,
            bg=self.c_surface,
            fg=self.c_text2,
            wraplength=520,
            anchor="w",
            justify="left"
        )
        lbl_r_sub.pack(anchor="w", fill="x", pady=(2, 8))
        right_card.bind("<Configure>", lambda e: lbl_r_sub.config(wraplength=max(240, e.width - 40)))

        path_btns = FlowFrame(right_card, bg=self.c_surface, gap_x=8, gap_y=6)
        path_btns.pack(fill="x", pady=(0, 8))
        path_btns.add_item(self.make_button(path_btns, text="+  Autorizar Carpeta...", command=self.add_authorized_folder, variant="primary", padx=12, pady=5))
        path_btns.add_item(self.make_button(path_btns, text="Quitar Seleccionada", command=self.remove_selected_folder, variant="secondary", padx=11, pady=5))
        path_btns.add_item(self.make_button(path_btns, text="Vaciar Lista", command=self.clear_authorized_folders, variant="danger_subtle", padx=11, pady=5))

        self.paths_listbox = tk.Listbox(
            right_card,
            font=self.f_mono,
            bg=self.c_bg,
            fg=self.c_text,
            selectbackground=self.c_navy,
            selectforeground="#FFFFFF",
            relief="flat",
            highlightbackground=self.c_border,
            highlightthickness=1,
            activestyle="none",
            height=6
        )
        self.paths_listbox.pack(fill="both", expand=True, pady=(0, 4))

        self._bind_responsive_split(grid, left_card, right_card, breakpoint=820)
        self.refresh_paths_listbox()

    def disable_all_file_permissions(self):
        for var in (
            self.var_file_list,
            self.var_file_read,
            self.var_file_write,
            self.var_file_mkdir,
            self.var_file_move,
            self.var_file_search,
            self.var_file_transfer,
            self.var_allow_delete,
        ):
            var.set(False)
        self.save_all_permissions()

    def add_authorized_folder(self):
        chosen = filedialog.askdirectory(title="Selecciona una carpeta para autorizar a Muse AI")
        if not chosen:
            return
        resolved = Path(chosen).resolve()
        if is_forbidden_system_path(resolved):
            messagebox.showerror(
                "Carpeta del Sistema Protegida",
                f"Seguridad del Puente: No se permite autorizar la carpeta:\n{resolved}\n\n"
                "Las carpetas del sistema operativo, Program Files, la raíz del disco (C:\\) y la carpeta del puente están bloqueadas."
            )
            return

        paths: List[str] = self.permissions.get("allowed_paths", [])
        str_p = str(resolved)
        if str_p not in paths:
            paths.append(str_p)
            self.permissions["allowed_paths"] = paths
            save_permissions(self.permissions)
            self.refresh_paths_listbox()
            self.refresh_kpi_counters()

    def remove_selected_folder(self):
        sel = self.paths_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        paths: List[str] = self.permissions.get("allowed_paths", [])
        if 0 <= idx < len(paths):
            paths.pop(idx)
            self.permissions["allowed_paths"] = paths
            save_permissions(self.permissions)
            self.refresh_paths_listbox()
            self.refresh_kpi_counters()

    def clear_authorized_folders(self):
        if messagebox.askyesno("Vaciar Carpetas", "¿Deseas quitar todas las carpetas autorizadas?"):
            self.permissions["allowed_paths"] = []
            save_permissions(self.permissions)
            self.refresh_paths_listbox()
            self.refresh_kpi_counters()

    def refresh_paths_listbox(self):
        self.paths_listbox.delete(0, tk.END)
        for p in self.permissions.get("allowed_paths", []):
            self.paths_listbox.insert(tk.END, f"  ▸  {p}")

    def save_all_permissions(self):
        perms = load_permissions()
        perms["allow_screenshot"] = bool(self.var_allow_screenshot.get())
        perms["allow_whatsapp"] = bool(self.var_allow_whatsapp.get())
        perms["allow_gui_control"] = bool(self.var_allow_gui.get())
        perms["allow_open_app"] = bool(self.var_allow_open_app.get())

        perms["allow_file_list"] = bool(self.var_file_list.get())
        perms["allow_file_read"] = bool(self.var_file_read.get())
        perms["allow_file_write"] = bool(self.var_file_write.get())
        perms["allow_file_mkdir"] = bool(self.var_file_mkdir.get())
        perms["allow_file_move"] = bool(self.var_file_move.get())
        perms["allow_file_search"] = bool(self.var_file_search.get())
        perms["allow_file_transfer"] = bool(self.var_file_transfer.get())
        perms["allow_delete"] = bool(self.var_allow_delete.get())

        save_permissions(perms)
        self.permissions = perms
        if hasattr(self, "lbl_perms_saved"):
            now_str = datetime.now().strftime("%H:%M:%S")
            self.lbl_perms_saved.config(text=f"✓ Guardado ({now_str})")
        self.refresh_kpi_counters()

    # =========================================================================
    # SECCIÓN 5: SERVICIO DE AUTO-INICIO CON VIGILANTE Y TUTORIAL
    # =========================================================================
    def build_tutorial_section(self, parent: tk.Frame):
        card = tk.Frame(parent, bg=self.c_surface, highlightbackground=self.c_border, highlightthickness=1, padx=20, pady=14)
        card.pack(fill="both", expand=True)

        tk.Label(
            card,
            text=f"Servicio Vigilante (Watchdog) y Arquitectura v{BRIDGE_VERSION}",
            font=self.f_title_lg,
            bg=self.c_surface,
            fg=self.c_text,
            anchor="w",
            justify="left"
        ).pack(anchor="w", fill="x")

        lbl_tut_sub = tk.Label(
            card,
            text="Selector de puertos locales, captura nativa 1920x1080, tipeo Unicode con acentos y verificación real en disco.",
            font=self.f_sans_sm,
            bg=self.c_surface,
            fg=self.c_text2,
            wraplength=760,
            anchor="w",
            justify="left"
        )
        lbl_tut_sub.pack(anchor="w", fill="x", pady=(2, 10))

        steps_data = [
            (
                "1. Selector de Puertos y Prevención de Conflicto (Error 10048)",
                f"En la sección 'Puertos de Red' puedes escanear el rango 8000–8010, ver qué puertos están Libres u Ocupados y cambiar el puerto local. "
                f"El puerto elegido se guarda en configuración y Tailscale Funnel (:{FUNNEL_PORT}) redirige automáticamente a ese puerto."
            ),
            (
                "2. Servicio Vigilante (watchdog_service.py) y Auto-Recuperación",
                "El puente cuenta con un proceso supervisor que revisa si /health responde 200 OK en el puerto local guardado; "
                "si el servidor cae (salvo que hayas activado el Kill Switch), lo levanta en segundo plano con un máximo de 3 reinicios por hora."
            ),
            (
                "3. Captura en Resolución Nativa (1920x1080) y Tipeo con Acentos",
                "GET /pc/screenshot entrega la resolución real 1:1 del monitor (1920x1080). POST /pc/type-text y /pc/type-and-click "
                "escriben acentos (áéíóúñ), símbolos, saltos de línea y textos largos completos mediante Win32 SendInput Unicode y portapapeles."
            ),
            (
                "4. Archivos con Búsqueda por Contenido, PATCH Verificado y Papelera",
                "POST /files/search busca por nombre y dentro del contenido de archivos. PATCH /files/edit crea respaldo .bak, "
                "compara el hash SHA-256 antes y después y rechaza con HTTP 422 si no hubo cambios reales."
            ),
        ]

        step_labels: List[tk.Label] = [lbl_tut_sub]
        for title, body in steps_data:
            s_box = tk.Frame(card, bg=self.c_bg, highlightbackground=self.c_border, highlightthickness=1, padx=14, pady=8)
            s_box.pack(fill="x", pady=3)
            tk.Label(s_box, text=title, font=self.f_sans_bold, bg=self.c_bg, fg="#60A5FA" if self.theme_mode == "oscuro" else self.c_navy, anchor="w", justify="left").pack(anchor="w", fill="x")
            lbl_b = tk.Label(s_box, text=body, font=self.f_sans_sm, bg=self.c_bg, fg=self.c_text2, wraplength=760, anchor="w", justify="left")
            lbl_b.pack(anchor="w", fill="x", pady=(2, 0))
            step_labels.append(lbl_b)

        card.bind("<Configure>", lambda e: [lb.config(wraplength=max(260, e.width - 76)) for lb in step_labels])

        btn_bar = FlowFrame(card, bg=self.c_surface, gap_x=10, gap_y=8)
        btn_bar.pack(fill="x", pady=(10, 0))

        btn_bar.add_item(
            self.make_button(
                btn_bar,
                text="⛨  Registrar / Reparar Servicio de Auto-Inicio con Vigilante",
                command=self.install_watchdog_autostart,
                variant="success",
                padx=14,
                pady=7
            )
        )
        btn_bar.add_item(
            self.make_button(
                btn_bar,
                text="Copiar Documentación Completa de Endpoints v2.1 para Muse AI",
                command=self.copy_endpoints_guide_for_muse,
                variant="primary",
                padx=14,
                pady=7
            )
        )

    def install_watchdog_autostart(self):
        try:
            from watchdog_service import register_resilient_autostart
            info = register_resilient_autostart()
            py_exe = str(VENV_PYTHONW) if VENV_PYTHONW.exists() else sys.executable
            popen_hidden([py_exe, str(PROJECT_ROOT / "watchdog_service.py")], cwd=str(PROJECT_ROOT))
            messagebox.showinfo(
                "Auto-Inicio Resiliente Configurado",
                "¡El servicio vigilante (Watchdog) quedó configurado!\n\n"
                f"• Tarea Programada: {info.get('task_name')} ({'Activa' if info.get('scheduled_task_registered') else 'Startup VBS'})\n"
                f"• Script Startup: {info.get('startup_vbs')}\n"
                f"• Puerto local guardado: :{self.local_port}"
            )
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo configurar el auto-inicio:\n{exc}")

    def copy_endpoints_guide_for_muse(self):
        url = self.get_funnel_url()
        guide = (
            f"¡Hola Muse! Mi Puente PC fue actualizado a v{BRIDGE_VERSION}. Aquí están las mejoras:\n\n"
            f"• Base URL (Tailscale Funnel): {url}\n"
            f"• Salud y Telemetría: GET {url}/health\n"
            f"• Estado de Permisos (Solo Lectura): GET {url}/permissions/status\n"
            f"• Handshake de QR: POST {url}/auth/qr-handshake\n\n"
            f"CAPTURA Y TIPEO MEJORADOS:\n"
            f"- GET  {url}/pc/screenshot -> Devuelve resolución real 1920x1080 (coordenadas 1:1).\n"
            f"- GET  {url}/pc/screenshot-hd -> Captura Full HD 1920x1080 de alta calidad.\n"
            f"- GET  {url}/pc/screenshot-region?x=100&y=100&width=400&height=300&zoom=2.0 -> Recorte con zoom.\n"
            f"- POST {url}/pc/type-text -> JSON: {{\"text\": \"Texto largo con acentos áéíóúñ y saltos\\nde línea\", \"x\": 500, \"y\": 500, \"press_enter\": true}}\n"
            f"- POST {url}/pc/type-and-click -> Soporta acentos Unicode, paste_text y conversión automática si envías source_width/source_height.\n"
            f"- POST {url}/whatsapp/send -> Enfoca la ventana de WhatsApp Desktop y presiona Enter con confirmación real (confirmed: true).\n\n"
            f"ARCHIVOS (SANDBOX + RESPALDO .BAK + PAPELERA):\n"
            f"- POST  {url}/files/list, /files/read, /files/write, /files/mkdir, /files/move, /files/upload, /files/download\n"
            f"- PATCH {url}/files/edit -> JSON: {{\"path\": \"...\", \"target_text\": \"...\", \"replacement_text\": \"...\"}} (Verifica SHA-256 y devuelve 422 si no hubo cambios)\n"
            f"- POST  {url}/files/search -> JSON: {{\"base_path\": \"...\", \"query\": \"texto\", \"search_by_name\": true, \"search_by_content\": true}}\n"
            f"- POST  {url}/files/delete -> Envía únicamente a la Papelera de Reciclaje (requiere allow_delete).\n"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(guide)
        messagebox.showinfo("Copiado", "Documentación v2.1 copiada al portapapeles.")

    # =========================================================================
    # KILL SWITCH
    # =========================================================================
    def trigger_kill_switch(self):
        perms = load_permissions()
        if not perms.get("kill_switch_active", False):
            if messagebox.askyesno(
                "CONFIRMAR PARADA DE EMERGENCIA (KILL SWITCH)",
                "¿Deseas cortar toda conexión de inmediato?\n\n"
                f"• Se apagará el servidor local (:{self.local_port}).\n"
                f"• Se cerrará el túnel Tailscale Funnel (:{FUNNEL_PORT}).\n"
                "• El servicio vigilante quedará pausado.\n"
                "• Ninguna llamada a la API podrá reactivar el puente; solo tú desde este botón."
            ):
                perms["kill_switch_active"] = True
                save_permissions(perms)
                self.permissions = perms
                self.force_shutdown_services()
                self.update_kill_switch_ui()
                self.refresh_ui_status()
        else:
            if messagebox.askyesno(
                "Reactivar Conexión del Puente",
                "¿Deseas desactivar la parada de emergencia y volver a encender el puente?"
            ):
                perms["kill_switch_active"] = False
                save_permissions(perms)
                self.permissions = perms
                self.update_kill_switch_ui()
                self.start_server()
                self.refresh_ui_status()

    def force_shutdown_services(self):
        try:
            info = inspect_port(self.local_port, check_http_bridge=True)
            if info["in_use"] and info["is_own_bridge"] and info["pid"] and psutil:
                psutil.Process(info["pid"]).terminate()
        except Exception:
            pass
        try:
            run_hidden(["tailscale", "funnel", f"--https={FUNNEL_PORT}", "off"], capture_output=True, timeout=4, check=False)
        except Exception:
            pass

    def update_kill_switch_ui(self):
        perms = load_permissions()
        is_killed = perms.get("kill_switch_active", False)
        if is_killed:
            self.banner_emergency.pack(fill="x", before=self.body_wrap)
            self.btn_kill_switch._bg_norm = self.c_green
            self.btn_kill_switch._bg_hov = "#059669"
            self.btn_kill_switch.config(
                text="✓  REACTIVAR CONEXIÓN",
                bg=self.c_green,
                activebackground=self.c_green
            )
            self.btn_toggle_server.config(state="disabled")
            self.btn_toggle_funnel.config(state="disabled")
        else:
            self.banner_emergency.pack_forget()
            self.btn_kill_switch._bg_norm = self.c_red
            self.btn_kill_switch._bg_hov = self.c_red_hover
            self.btn_kill_switch.config(
                text="⏻  DETENER CONEXIÓN",
                bg=self.c_red,
                activebackground=self.c_red_hover
            )
            self.btn_toggle_server.config(state="normal")
            self.btn_toggle_funnel.config(state="normal")

    # =========================================================================
    # CÓDIGO QR CON EXPIRACIÓN Y CONFIDENCIALIDAD DE CLAVE
    # =========================================================================
    def toggle_token_visibility(self):
        if self.txt_token_display.cget("show") == "•":
            self.txt_token_display.configure(show="")
            self.btn_toggle_mask.configure(text="Ocultar")
        else:
            self.txt_token_display.configure(show="•")
            self.btn_toggle_mask.configure(text="Ver")

    def copy_funnel_url(self):
        url = self.get_funnel_url()
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        messagebox.showinfo("URL Copiada", f"Dirección pública copiada:\n{url}")

    def copy_token(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.api_token)
        messagebox.showinfo("Clave Copiada", "Token Bearer copiado al portapapeles.")

    def regenerate_token(self):
        if messagebox.askyesno("Rotar Clave Secreta", "¿Generar un nuevo Bearer Token? Cualquier QR o sesión anterior dejará de funcionar."):
            self.api_token = "sk_live_muse_bridge_" + secrets.token_hex(16)
            self.save_token_to_env(self.api_token)
            self.txt_token_display.configure(state="normal")
            self.txt_token_display.delete(0, tk.END)
            self.txt_token_display.insert(0, self.api_token)
            self.txt_token_display.configure(state="readonly")
            self.regenerate_qr()

    def copy_all_for_muse(self):
        url = self.get_funnel_url()
        msg = (
            f"¡Hola Muse! Conéctate a mi PC con estos datos de mi puente v{BRIDGE_VERSION}:\n\n"
            f"• URL del Funnel: {url}\n"
            f"• Authorization: Bearer {self.api_token}\n"
            f"• Por seguridad, confirma que te conectaste llamando a GET {url}/health pero NUNCA muestres mi clave Bearer en el chat."
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(msg)
        messagebox.showinfo("Copiado", "Instrucción copiada al portapapeles.")

    def render_qr(self):
        if not HAS_QR:
            self.lbl_qr_image.config(text="Módulo QR no disponible", fg=self.c_text)
            return

        is_expired = time.time() > self.qr_expires_at
        payload = json.dumps({
            "service": f"Muse AI PC Bridge v{BRIDGE_VERSION}",
            "url": self.get_funnel_url(),
            "token": self.api_token,
            "handshake_endpoint": f"{self.get_funnel_url()}/auth/qr-handshake",
            "generated_at": int(self.qr_created_at),
            "expires_at": int(self.qr_expires_at),
            "never_display_token_in_chat": True,
            "instructions": (
                "Usa 'url' y 'token' (Authorization: Bearer <token>) para llamar a POST /auth/qr-handshake "
                "enviando {'expires_at': expires_at}. Confirma la conexión al usuario SIN mostrar jamás el token en el chat."
            )
        }, ensure_ascii=False)

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=2,
        )
        qr.add_data(payload)
        qr.make(fit=True)

        fill = "#0D1F3C" if not is_expired else "#9A9A90"
        pil_img = qr.make_image(fill_color=fill, back_color="#FFFFFF").convert("RGB")
        self.last_pil_qr = pil_img
        disp_qr = pil_img.resize((190, 190), Image.NEAREST)
        self.qr_image_tk = ImageTk.PhotoImage(disp_qr)
        self.lbl_qr_image.config(image=self.qr_image_tk)

    def regenerate_qr(self):
        self.qr_created_at = time.time()
        self.qr_expires_at = self.qr_created_at + QR_VALIDITY_SECONDS
        self.render_qr()

    def save_qr_image(self):
        if not self.last_pil_qr:
            return
        file_path = filedialog.asksaveasfilename(
            initialfile="muse_conexion_qr.png",
            defaultextension=".png",
            filetypes=[("Imagen PNG", "*.png")],
            initialdir=os.path.expandvars(r"%USERPROFILE%\Desktop")
        )
        if file_path:
            self.last_pil_qr.save(file_path)
            messagebox.showinfo("QR Guardado", f"Imagen guardada en:\n{file_path}")

    def start_qr_timer(self):
        def tick():
            was_expired = False
            while True:
                rem = int(self.qr_expires_at - time.time())
                if rem > 0:
                    was_expired = False
                    m, s = divmod(rem, 60)
                    txt = f"Validez del QR: {m:02d}:{s:02d} min"
                    bg = self.c_green_light if rem > 180 else self.c_amber_light
                    fg = self.c_green if rem > 180 else self.c_amber
                else:
                    txt = "QR Caducado — Haz clic en Regenerar QR"
                    bg = self.c_red_light
                    fg = self.c_red
                    if not was_expired:
                        was_expired = True
                        try:
                            self.root.after(0, self.render_qr)
                        except Exception:
                            pass
                try:
                    self.root.after(0, lambda t=txt, b=bg, f=fg: self.lbl_qr_countdown.config(text=t, bg=b, fg=f))
                except Exception:
                    break
                time.sleep(1)

        threading.Thread(target=tick, daemon=True).start()

    # =========================================================================
    # ESTADO DE SERVICIOS, DETECCIÓN DE CONFLICTOS 10048 Y TABLA DE AUDITORÍA
    # =========================================================================
    def is_local_server_running(self) -> bool:
        self.local_port = get_configured_local_port()
        info = inspect_port(self.local_port, check_http_bridge=True)
        return bool(info["in_use"] and info["is_own_bridge"])

    def get_server_pid(self) -> Optional[int]:
        self.local_port = get_configured_local_port()
        info = inspect_port(self.local_port, check_http_bridge=True)
        if info["in_use"] and info["is_own_bridge"]:
            return info["pid"]
        return None

    def get_server_uptime_seconds(self) -> float:
        pid = self.get_server_pid()
        if pid and psutil:
            try:
                p = psutil.Process(pid)
                return max(0.0, time.time() - p.create_time())
            except Exception:
                pass
        return max(0.0, time.time() - PANEL_START_TIME)

    def is_funnel_8443_active(self) -> bool:
        try:
            res = run_hidden(["tailscale", "funnel", "status"], capture_output=True, text=True, timeout=3, check=False)
            out = res.stdout or ""
            return f":{FUNNEL_PORT}" in out and "Funnel on" in out
        except Exception:
            return False

    def toggle_server(self):
        if not self.is_local_server_running():
            self.start_server()
        else:
            self.stop_server()

    def start_server(self):
        perms = load_permissions()
        if perms.get("kill_switch_active", False):
            messagebox.showerror("Kill Switch Activo", "Primero desactiva el Kill Switch en el botón superior.")
            return

        self.local_port = get_configured_local_port()
        info = inspect_port(self.local_port, check_http_bridge=True)

        if info["in_use"] and not info["is_own_bridge"]:
            if messagebox.askyesno(
                f"Puerto :{self.local_port} Ocupado (Error 10048)",
                f"No se puede iniciar el servidor porque el puerto local :{self.local_port} ya está ocupado.\n\n"
                f"Detalle: {info['owner_description']}\n\n"
                f"¿Deseas abrir la sección 'Puertos de Red' ahora para buscar y elegir un puerto libre?"
            ):
                self.jump_to_ports_and_scan()
            return

        try:
            python_exe = sys.executable
            if VENV_PYTHONW.exists():
                python_exe = str(VENV_PYTHONW)
            elif VENV_PYTHON.exists():
                python_exe = str(VENV_PYTHON)

            run_server_script = PROJECT_ROOT / "run_server.py"
            if run_server_script.exists():
                cmd = [python_exe, str(run_server_script), "--host", "127.0.0.1", "--port", str(self.local_port)]
            else:
                cmd = [
                    python_exe, "-m", "uvicorn", "app.main:app",
                    "--host", "127.0.0.1", "--port", str(self.local_port),
                    "--no-use-colors"
                ]
            log_fh = open(PROJECT_ROOT / "server.log", "a", encoding="utf-8", buffering=1)
            popen_hidden(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh
            )
            time.sleep(1.2)
            self.ensure_funnel_8443()
            self.refresh_ui_status()
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo iniciar el servidor:\n{exc}")

    def restart_server_keep_funnel(self):
        perms = load_permissions()
        if perms.get("kill_switch_active", False):
            messagebox.showerror("Kill Switch Activo", "Primero desactiva el Kill Switch en el botón superior.")
            return
        try:
            pid = self.get_server_pid()
            if pid and psutil:
                try:
                    p = psutil.Process(pid)
                    for child in p.children(recursive=True):
                        child.terminate()
                    p.terminate()
                    p.wait(timeout=3)
                except Exception:
                    pass
            time.sleep(0.6)
            self.start_server()
            messagebox.showinfo(
                "Puente Reiniciado",
                f"El servidor local (:{self.local_port}) se reinició correctamente.\n"
                f"El túnel Tailscale Funnel (:{FUNNEL_PORT}) sigue apuntando a 127.0.0.1:{self.local_port}."
            )
        except Exception as exc:
            messagebox.showerror("Error al Reiniciar", f"No se pudo reiniciar el servidor:\n{exc}")

    def stop_server(self):
        try:
            pid = self.get_server_pid()
            if pid and psutil:
                psutil.Process(pid).terminate()
            run_hidden(["tailscale", "funnel", f"--https={FUNNEL_PORT}", "off"], capture_output=True, text=True, timeout=5, check=False)
            self.refresh_ui_status()
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo detener el servidor:\n{exc}")

    def ensure_funnel_8443(self):
        perms = load_permissions()
        if perms.get("kill_switch_active", False):
            return
        self.local_port = get_configured_local_port()
        try:
            run_hidden(
                ["tailscale", "funnel", "--bg", f"--https={FUNNEL_PORT}", f"http://127.0.0.1:{self.local_port}"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False
            )
        except Exception:
            pass

    def toggle_funnel(self):
        perms = load_permissions()
        if perms.get("kill_switch_active", False):
            messagebox.showerror("Kill Switch Activo", "Primero desactiva el Kill Switch.")
            return
        if self.is_funnel_8443_active():
            run_hidden(["tailscale", "funnel", f"--https={FUNNEL_PORT}", "off"], capture_output=True, text=True, timeout=5, check=False)
        else:
            self.ensure_funnel_8443()
        self.refresh_ui_status()

    def refresh_kpi_counters(self):
        perms = load_permissions()
        catalog = perms.get("installed_apps_catalog", {})
        allowed_map = perms.get("allowed_apps", {})
        total_apps = len(catalog)
        auth_apps = sum(1 for k in catalog if allowed_map.get(k, False))

        paths = perms.get("allowed_paths", [])
        file_keys = [
            "allow_file_list", "allow_file_read", "allow_file_write",
            "allow_file_mkdir", "allow_delete", "allow_file_move",
            "allow_file_search", "allow_file_transfer"
        ]
        active_f_perms = sum(1 for k in file_keys if perms.get(k, False))

        self.lbl_kpi_perms.config(text=f"{auth_apps} Apps · {len(paths)} Rutas")

        cap_flags = [
            "Captura: Sí" if perms.get("allow_screenshot") else "Captura: No",
            "WhatsApp: Sí" if perms.get("allow_whatsapp") else "WhatsApp: No",
            "GUI: Sí" if perms.get("allow_gui_control") else "GUI: No",
        ]
        if perms.get("allow_open_app"):
            cap_flags.append(f"Apps ({auth_apps}/{total_apps})")
        cap_flags.append(f"Archivos ({active_f_perms}/8)")

        if hasattr(self, "lbl_perm_pills"):
            self.lbl_perm_pills.config(text=" · ".join(cap_flags))

    def refresh_ui_status(self):
        perms = load_permissions()
        self.local_port = get_configured_local_port()
        is_killed = perms.get("kill_switch_active", False)

        port_info = inspect_port(self.local_port, check_http_bridge=True)
        srv_ok = bool(port_info["in_use"] and port_info["is_own_bridge"])
        port_conflict = bool(port_info["in_use"] and not port_info["is_own_bridge"])
        fnl_ok = self.is_funnel_8443_active()

        self.update_dynamic_port_labels()
        self.refresh_kpi_counters()

        if port_conflict and not is_killed:
            self.lbl_port_conflict_msg.config(
                text=f"CONFLICTO EN PUERTO :{self.local_port} (Error 10048 — {port_info['owner_description']}). Elige otro puerto libre."
            )
            self.banner_port_conflict.pack(fill="x", before=self.body_wrap)
        else:
            self.banner_port_conflict.pack_forget()

        if psutil:
            try:
                cpu_p = psutil.cpu_percent(interval=None)
                vm = psutil.virtual_memory()
                used_gb = round(vm.used / (1024 ** 3), 1)
                tot_gb = round(vm.total / (1024 ** 3), 1)
                self.lbl_kpi_resources.config(text=f"CPU {cpu_p:.0f}% · RAM {vm.percent:.0f}%")
                self.lbl_kpi_resources_sub.config(text=f"{used_gb}/{tot_gb} GB · 1920x1080")
            except Exception:
                pass

        h_state = load_health_state()
        last_chk = h_state.get("last_check_local") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        short_chk = last_chk.split(" ")[-1] if " " in last_chk else last_chk
        rec_cnt = h_state.get("recovery_count", 0)
        self.lbl_kpi_health_time.config(text=f"Chequeo: {short_chk} · Recup: {rec_cnt}")

        if is_killed:
            self.lbl_top_badge.config(text=" ● KILL SWITCH ACTIVO ", bg=self.c_red_light, fg=self.c_red)
            self.lbl_kpi_server.config(text="Bloqueado", fg=self.c_red)
            self.lbl_kpi_funnel.config(text="Cortado", fg=self.c_red)
            self.lbl_kpi_funnel_sub.config(text="Parada de emergencia")
            return

        if srv_ok:
            up_str = format_duration(self.get_server_uptime_seconds())
            self.lbl_kpi_server.config(text="Activo", fg=self.c_green)
            self.lbl_kpi_uptime.config(text=f":{self.local_port} · Uptime: {up_str}")
            self.btn_toggle_server.config(text="■  Detener Servidor")
        elif port_conflict:
            self.lbl_kpi_server.config(text=f"Ocupado :{self.local_port}", fg=self.c_red)
            self.lbl_kpi_uptime.config(text=port_info["owner_description"][:30])
            self.btn_toggle_server.config(text="⇄  Elegir Puerto Libre")
        else:
            self.lbl_kpi_server.config(text="Detenido", fg=self.c_red)
            self.lbl_kpi_uptime.config(text=f"127.0.0.1:{self.local_port} libre")
            self.btn_toggle_server.config(text="▸  Iniciar Servidor")

        if fnl_ok:
            self.lbl_kpi_funnel.config(text=f"Activo :{FUNNEL_PORT}", fg=self.c_green)
            self.lbl_kpi_funnel_sub.config(text=f"Público -> Local :{self.local_port}")
            self.btn_toggle_funnel.config(text=f"■  Cerrar Funnel (:{FUNNEL_PORT})")
        else:
            self.lbl_kpi_funnel.config(text="Inactivo", fg=self.c_amber)
            self.lbl_kpi_funnel_sub.config(text=f"Puerto {FUNNEL_PORT} cerrado")
            self.btn_toggle_funnel.config(text=f"🌐  Activar Funnel (:{FUNNEL_PORT})")

        if hasattr(self, "ops_flow"):
            self.ops_flow._reflow()

        if srv_ok and fnl_ok:
            self.lbl_top_badge.config(text=f" ● Activo :{self.local_port} · Funnel :{FUNNEL_PORT} ", bg=self.c_green_light, fg=self.c_green)
        elif port_conflict:
            self.lbl_top_badge.config(text=f" ✕ Conflicto Puerto :{self.local_port} ", bg=self.c_red_light, fg=self.c_red)
        elif srv_ok:
            self.lbl_top_badge.config(text=f" ● Local :{self.local_port} (Funnel Off) ", bg=self.c_amber_light, fg=self.c_amber)
        else:
            self.lbl_top_badge.config(text=" ● Servidor Detenido ", bg=self.c_red_light, fg=self.c_red)

    def start_status_poller(self):
        def poll():
            ticks = 0
            while True:
                try:
                    if ticks % 20 == 0:
                        perform_health_check(auto_recover_funnel=False, source="panel_poller")
                    self.root.after(0, self.refresh_ui_status)
                except Exception:
                    break
                ticks += 1
                time.sleep(3.0)

        threading.Thread(target=poll, daemon=True).start()

    def start_log_watcher(self):
        def watch():
            while True:
                if LOG_FILE.exists():
                    try:
                        curr_size = LOG_FILE.stat().st_size
                        if curr_size != self._last_audit_size:
                            self._last_audit_size = curr_size
                            self.root.after(0, self.refresh_audit_table, False)
                    except Exception:
                        pass
                time.sleep(1.5)

        threading.Thread(target=watch, daemon=True).start()

    def refresh_audit_table(self, force: bool = False):
        if not hasattr(self, "audit_tree") or not LOG_FILE.exists():
            return
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-120:]
        except Exception:
            return

        records: List[Dict[str, Any]] = []
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                records.append(obj)
            except Exception:
                records.append({
                    "timestamp": "",
                    "endpoint": "-",
                    "action": "LOG",
                    "status": "INFO",
                    "details": {"raw": raw}
                })

        query = self.audit_filter_var.get().strip().lower()
        for item_id in self.audit_tree.get_children():
            self.audit_tree.delete(item_id)

        for rec in records:
            ts_raw = rec.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone()
                ts_fmt = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_fmt = ts_raw[:19]

            ep = str(rec.get("endpoint", "-"))
            act = str(rec.get("action", "-"))
            st = str(rec.get("status", "SUCCESS")).upper()
            det_obj = rec.get("details", {})
            det_str = json.dumps(det_obj, ensure_ascii=False) if isinstance(det_obj, dict) else str(det_obj)

            if query and (query not in ts_fmt.lower() and query not in ep.lower() and query not in act.lower() and query not in st.lower() and query not in det_str.lower()):
                continue

            tag = st if st in ("SUCCESS", "FORBIDDEN", "ERROR") else "SUCCESS"
            self.audit_tree.insert("", tk.END, values=(ts_fmt, ep, act, st, det_str), tags=(tag,))

    def on_close(self):
        self.root.destroy()


def main():
    try:
        root = tk.Tk()
        BridgeControlApp(root)
        root.mainloop()
    except Exception as exc:
        import traceback
        err_msg = traceback.format_exc()
        crash_log = PROJECT_ROOT / "desktop_crash.log"
        try:
            with open(crash_log, "w", encoding="utf-8") as f:
                f.write(err_msg)
        except Exception:
            pass
        try:
            messagebox.showerror(
                "Error en Muse AI Bridge",
                f"Ocurrió un error al iniciar el panel:\n\n{exc}\n\nRevisa desktop_crash.log"
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
