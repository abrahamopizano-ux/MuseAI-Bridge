import os
import secrets
from typing import Dict, List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    API_SECRET_TOKEN: str = f"sk_live_muse_bridge_{secrets.token_hex(16)}"
    SERVER_HOST: str = "127.0.0.1"
    SERVER_PORT: int = 8000
    DEBUG: bool = False

    SCREENSHOT_MAX_WIDTH: int = 1920
    SCREENSHOT_QUALITY: int = 85

    WHATSAPP_PROVIDER: str = "windows_protocol"
    BAILEYS_API_URL: str = "http://127.0.0.1:3000/send-message"

    ALLOWED_APPS: Dict[str, List[str]] = {
        "notepad": ["notepad.exe"],
        "calculator": ["calc.exe"],
        "terminal": ["cmd.exe"],
        "powershell": ["powershell.exe"],
        "chrome": ["chrome.exe"],
        "edge": ["msedge.exe"],
        "explorer": ["explorer.exe"],
        "whatsapp": ["cmd.exe", "/c", "start", "whatsapp:"],
        "antigravity": [os.path.expandvars(r"%LOCALAPPDATA%\Programs\antigravity\Antigravity.exe")]
    }

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
