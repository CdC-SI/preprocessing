"""Logging configuration — called ONCE, by the CLI only.

Replaces the scattered logging.basicConfig calls (29 files initially).
No library module configures logging: they use logging.getLogger(__name__) and the entry point makes the decision.
"""

from __future__ import annotations

import logging


def configure_logging(verbosity: int = 0) -> None:
    """-1 = WARNING., 0 = INFO, 1+ = DEBUG"""
    if verbosity <= -1:
        level = logging.WARNING
    elif verbosity == 0:
        level = logging.INFO
    else:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
