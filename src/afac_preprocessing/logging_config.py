"""Configuration du logging — appelée UNE fois, par la CLI seule.

Remplace les ``logging.basicConfig`` dispersés (29 fichiers au départ).
Aucun module de bibliothèque ne configure le logging : ils font
``logging.getLogger(__name__)`` et c'est le point d'entrée qui décide.
"""

from __future__ import annotations

import logging


def configure_logging(verbosity: int = 0) -> None:
    """0 = INFO (comportement historique des scripts), 1+ = DEBUG, -1 = WARNING."""
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
