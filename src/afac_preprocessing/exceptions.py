"""Hiérarchie d'erreurs métier du pipeline.

Le noyau lève ces exceptions — jamais ``sys.exit`` ni ``SystemExit``
(invariant n°3 du refactor). Seule la CLI (``cli/main.py``) et la couche de
compat ``utils/`` traduisent en codes de sortie.
"""

from __future__ import annotations


class AfacError(Exception):
    """Base commune de toutes les erreurs métier du pipeline."""


class ConfigError(AfacError):
    """Configuration invalide ou incomplète (.env, variables, chemins)."""


class StepInputMissing(AfacError):
    """Une entrée déclarée d'une étape est absente du workspace."""


class StepFailed(AfacError):
    """Une étape a échoué pendant son exécution."""


class VlmUnavailable(AfacError):
    """Le client VLM est requis mais indisponible (URL absente ou injoignable)."""


class EmbeddingUnavailable(AfacError):
    """Le client d'embedding est requis mais indisponible."""


class UnknownStep(AfacError):
    """Nom d'étape inconnu du registre."""
