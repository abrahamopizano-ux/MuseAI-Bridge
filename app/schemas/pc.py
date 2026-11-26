from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class OpenAppRequest(BaseModel):
    app_name: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Identificador o nombre de la aplicación instalada y autorizada por el usuario en el panel."
    )


class GuiActionType(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    MOVE_TO = "move_to"
    TYPE_TEXT = "type_text"
    PASTE_TEXT = "paste_text"
    PRESS_KEY = "press_key"
    HOTKEY = "hotkey"
    SLEEP = "sleep"


class GuiAction(BaseModel):
    action: GuiActionType = Field(..., description="Tipo de acción a ejecutar.")
    x: Optional[int] = Field(None, ge=0, description="Coordenada X en pantalla (píxeles).")
    y: Optional[int] = Field(None, ge=0, description="Coordenada Y en pantalla (píxeles).")
    text: Optional[str] = Field(None, max_length=100000, description="Texto completo (soporta acentos, ñ, símbolos, saltos de línea y textos largos).")
    key: Optional[str] = Field(None, description="Tecla individual (ej: enter, esc, tab, f5, backspace).")
    keys: Optional[List[str]] = Field(None, description="Lista de teclas para combinación (ej: ['ctrl', 'c']).")
    duration: float = Field(0.0, ge=0.0, le=10.0, description="Duración del movimiento o pausa en segundos.")
    press_enter: bool = Field(False, description="Si es True tras type_text/paste_text, presiona Enter al finalizar.")

    @model_validator(mode="after")
    def validate_action_fields(self):
        if self.action in {GuiActionType.CLICK, GuiActionType.DOUBLE_CLICK, GuiActionType.RIGHT_CLICK, GuiActionType.MOVE_TO}:
            if (self.x is None and self.y is not None) or (self.x is not None and self.y is None):
                raise ValueError("Para acciones de cursor se deben enviar tanto 'x' como 'y', o ninguno para operar en la posición actual.")
        if self.action in {GuiActionType.TYPE_TEXT, GuiActionType.PASTE_TEXT} and (self.text is None or self.text == ""):
            raise ValueError(f"La acción '{self.action}' requiere el campo 'text'.")
        if self.action == GuiActionType.PRESS_KEY and not self.key:
            raise ValueError("La acción 'press_key' requiere el campo 'key'.")
        if self.action == GuiActionType.HOTKEY and (not self.keys or len(self.keys) < 2):
            raise ValueError("La acción 'hotkey' requiere al menos dos teclas en el campo 'keys'.")
        return self


class TypeAndClickBatchRequest(BaseModel):
    actions: List[GuiAction] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Secuencia ordenada de acciones GUI a ejecutar secuencialmente."
    )
    source_width: Optional[int] = Field(
        None,
        ge=320,
        le=7680,
        description="Ancho de referencia de la imagen donde se calcularon las coordenadas (si difiere del monitor real, el puente las convierte automáticamente)."
    )
    source_height: Optional[int] = Field(
        None,
        ge=240,
        le=4320,
        description="Alto de referencia de la imagen donde se calcularon las coordenadas."
    )


class TypeLongTextRequest(BaseModel):
    text: str = Field(
        ...,
        min_length=1,
        max_length=200000,
        description="Texto largo completo con soporte nativo para acentos (áéíóúñ), símbolos y saltos de línea."
    )
    x: Optional[int] = Field(None, ge=0, description="Coordenada X opcional para hacer clic antes de escribir.")
    y: Optional[int] = Field(None, ge=0, description="Coordenada Y opcional para hacer clic antes de escribir.")
    press_enter: bool = Field(False, description="Si es True, presiona Enter al terminar de insertar el texto.")
    use_clipboard: bool = Field(True, description="Si es True, inserta de forma instantánea vía portapapeles o SendInput Unicode.")


class ScreenshotResponse(BaseModel):
    confirmed: bool = True
    format: str = "image/jpeg"
    base64_data: str
    width: int
    height: int
    native_width: int
    native_height: int
    scale_factor_x: float = 1.0
    scale_factor_y: float = 1.0
    region_offset_x: int = 0
    region_offset_y: int = 0
    zoom_applied: float = 1.0


class SystemInfoResponse(BaseModel):
    confirmed: bool = True
    platform: str
    cpu_percent: float
    cpu_count: int
    memory_total_gb: float
    memory_used_gb: float
    memory_percent: float
    disk_percent: float
    uptime_seconds: float
    bridge_uptime_seconds: Optional[float] = None
    native_screen_width: Optional[int] = None
    native_screen_height: Optional[int] = None
