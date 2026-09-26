"""Console and rotating-file logging with secret redaction.

Tracebacks are logged for diagnosis; every formatted line, including exception
text from model providers, passes through redaction before it is written.
"""
import logging
import logging.handlers
import os
import re

from .qgis_runtime import SECRET_ENV

PATTERNS = [re.compile(p, re.I) for p in (
    r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}",
    r"((?:api[_-]?key|authorization|token)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{6,}",
    r"()\bsk-[A-Za-z0-9_-]{12,}",
)]
HANDLER_NAME = "ftth"


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt, secrets=()):
        super().__init__(fmt)
        self.secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def format(self, record):
        return redact(super().format(record), self.secrets)


def redact(text, secrets=()):
    for secret in secrets:
        text = text.replace(secret, "***")
    for pattern in PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "***", text)
    return text


def configure(settings, console=True):
    """Idempotent: re-running replaces this module's handlers, not others'."""
    directory = settings.data / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    secrets = [settings.token, settings.api_key, *(os.environ.get(k, "") for k in SECRET_ENV)]
    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", secrets)
    handlers = [logging.handlers.RotatingFileHandler(directory / "backend.log", maxBytes=5 * 1024 * 1024,
                                                     backupCount=5, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())
    level = getattr(logging, settings.log_level.upper())
    for name in ("ftth", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for handler in [h for h in logger.handlers if h.get_name() == HANDLER_NAME]:
            logger.removeHandler(handler)
            handler.close()
        if name in ("ftth", "uvicorn"):
            for handler in handlers:
                handler.set_name(HANDLER_NAME)
                handler.setFormatter(formatter)
                logger.addHandler(handler)
        # The plugin polls several times a second; access lines only at debug.
        logger.setLevel(max(level, logging.WARNING) if name == "uvicorn.access" and level > logging.DEBUG else level)
        logger.propagate = name not in ("ftth", "uvicorn")
    return directory / "backend.log"
