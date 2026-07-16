import sys
import io
import os
from loguru import logger

# Wrap stdout with UTF-8 so emoji in log messages don't crash CP1252
# Windows terminals
if hasattr(sys.stdout, "buffer"):
    _stdout_sink = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        errors="replace",
        line_buffering=True)
else:
    _stdout_sink = sys.stdout

logger.remove()

logger.add(
    _stdout_sink,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level="INFO",
)

os.makedirs("logs", exist_ok=True)
logger.add(
    "logs/app.log",
    rotation="100 MB",
    retention="10 days",
    level="DEBUG",
    encoding="utf-8",
)

__all__ = ["logger"]
