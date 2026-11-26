import logging
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIT_LOG_FILE = PROJECT_ROOT / "audit.log"

audit_logger = logging.getLogger("audit_logger")
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = False

if not audit_logger.handlers:
    file_handler = logging.FileHandler(AUDIT_LOG_FILE, encoding="utf-8")
    formatter = logging.Formatter("%(message)s")
    file_handler.setFormatter(formatter)
    audit_logger.addHandler(file_handler)

    # En pythonw.exe (arranque oculto sin consola), sys.stderr es None.
    # Solo agregar StreamHandler si sys.stderr existe y es un stream válido.
    if sys.stderr is not None and hasattr(sys.stderr, "write"):
        try:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(formatter)
            audit_logger.addHandler(console_handler)
        except Exception:
            pass


def log_audit_event(
    client_ip: str,
    endpoint: str,
    action: str,
    details: dict,
    status: str = "SUCCESS"
) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "client_ip": client_ip,
        "endpoint": endpoint,
        "action": action,
        "status": status,
        "details": details
    }
    try:
        audit_logger.info(json.dumps(record, ensure_ascii=False))
    except Exception:
        pass
