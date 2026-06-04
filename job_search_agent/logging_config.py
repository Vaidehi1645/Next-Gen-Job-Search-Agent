import logging
from typing import Optional


def get_logger(name: Optional[str] = None) -> logging.Logger:
    name = name or "job_search_agent"
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


# module-level default logger
logger = get_logger("job_search_agent")
import logging

logger = logging.getLogger("job_search_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

def configure(level: str | int = logging.INFO) -> None:
    logger.setLevel(level)
