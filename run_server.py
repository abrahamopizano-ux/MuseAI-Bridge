"""
Lanzador seguro de producción para Uvicorn bajo python.exe o pythonw.exe (sin consola).
Lee automáticamente el puerto local guardado en la configuración (permissions.json / .env),
a menos que se pase explícitamente --port <N> por línea de comandos.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SERVER_LOG_FILE = PROJECT_ROOT / "server.log"


def _ensure_valid_stdio():
    """Garantiza que sys.stdout, sys.stderr y sys.stdin nunca sean None bajo pythonw.exe."""
    try:
        if sys.stdout is None or not hasattr(sys.stdout, "write"):
            sys.stdout = open(SERVER_LOG_FILE, "a", encoding="utf-8", buffering=1)
        if sys.stderr is None or not hasattr(sys.stderr, "write"):
            sys.stderr = open(SERVER_LOG_FILE, "a", encoding="utf-8", buffering=1)
        if sys.stdin is None:
            sys.stdin = open(os.devnull, "r", encoding="utf-8")
    except Exception:
        pass


_ensure_valid_stdio()

import uvicorn
from app.config import settings
from app.core.port_utils import get_configured_local_port
from app.main import app


def main():
    port = get_configured_local_port()
    host = settings.SERVER_HOST

    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass
        elif arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]

    log_config = uvicorn.config.LOGGING_CONFIG.copy()
    if "formatters" in log_config:
        for fmt in log_config["formatters"].values():
            fmt["use_colors"] = False

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        workers=1,
        log_config=log_config,
        access_log=True,
    )


if __name__ == "__main__":
    main()
