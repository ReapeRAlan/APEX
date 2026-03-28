import logging
import sys
from pathlib import Path
from datetime import datetime


def setup_apex_logger():
    logger = logging.getLogger("apex")
    logger.setLevel(logging.DEBUG)

    # Handler de consola con color (stderr para que uvicorn lo muestre)
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.DEBUG)

    class ColorFormatter(logging.Formatter):
        COLORS = {
            "DEBUG":    "\033[36m",   # cyan
            "INFO":     "\033[32m",   # verde
            "WARNING":  "\033[33m",   # amarillo
            "ERROR":    "\033[31m",   # rojo
            "CRITICAL": "\033[35m",   # magenta
        }
        RESET = "\033[0m"

        def format(self, record):
            color = self.COLORS.get(record.levelname, "")
            record.levelname = f"{color}{record.levelname}{self.RESET}"
            return super().format(record)

    ch.setFormatter(ColorFormatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(ch)

    # Handler de archivo para audit trail
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    fh = logging.FileHandler(
        log_dir / f"apex_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    ))
    logger.addHandler(fh)
    return logger


log = setup_apex_logger()
