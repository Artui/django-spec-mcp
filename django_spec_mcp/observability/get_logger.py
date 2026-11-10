from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    """``logging.getLogger`` for a module inside this package."""
    return logging.getLogger(name)


__all__ = ["get_logger"]
