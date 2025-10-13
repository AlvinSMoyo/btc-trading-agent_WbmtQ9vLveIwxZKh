# app/logs/setup.py
from __future__ import annotations
import logging, os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

def init_logging():
    log_dir = Path("logs"); log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "agent.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Console
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(ch)

    # Daily rolling file (keep 7 days)
    if not any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers):
        fh = TimedRotatingFileHandler(str(log_path), when="midnight", backupCount=7, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.info("logging initialized")
