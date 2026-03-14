"""Logging helpers for MAS."""

import logging

LOGGER_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def get_logger(name: str = "MAS", level: int = logging.DEBUG) -> logging.Logger:
    """Get logger instance with given name."""
    logging.basicConfig(level=level, format=LOGGER_FMT)
    logger = logging.getLogger(name)
    return logger
