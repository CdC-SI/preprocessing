"""Shared path and environment helpers for all pipeline scripts.

⚠ COUCHE DE COMPAT (lot 3 du refactor) — façade mince au-dessus du noyau
(``settings.py`` / ``workspace.py``). Les signatures publiques, les valeurs de
retour et les messages d'erreur sont conservés à l'identique : les 20 scripts
appelants ne sont pas modifiés. Cette couche garde le droit de lire
``os.environ`` et de lever ``SystemExit`` (zone exemptée de l'invariant n°3) ;
elle disparaît au lot 8, quand les étapes converties (lot 6) cesseront de
l'appeler.

Le noyau lève ``ConfigError`` ; **seule la façade traduit** en ``SystemExit``,
avec le même message qu'avant le refactor.
"""
import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from ..exceptions import ConfigError
from ..settings import _find_project_root

_log = logging.getLogger(__name__)


def project_root() -> Path:
    """Return the project root (the directory that contains pyproject.toml).

    Respects the PROJECT_ROOT env variable so Tekton/container deployments can
    override the auto-detected path without rebuilding the image.

    Délègue au même helper que ``Settings.project_root`` (source unique).
    """
    if "PROJECT_ROOT" in os.environ:
        return Path(os.environ["PROJECT_ROOT"]).resolve()
    return _find_project_root()


def _core_load_env(dotenv_path: Path) -> None:
    """Noyau : charge *dotenv_path* dans l'environnement, lève ConfigError."""
    resolved = dotenv_path.resolve()
    if not resolved.exists():
        raise ConfigError(f"Error: .env file not found — {resolved}")
    load_dotenv(dotenv_path=resolved)
    _log.info("Environment loaded from: %s", resolved)


def load_env(dotenv_path: Path) -> None:
    """Load *dotenv_path* into the process environment.

    Raises SystemExit (not an exception) if the file is absent so the error
    message stays clean in CLI output.
    """
    try:
        _core_load_env(dotenv_path)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


def resolve_input_pdf(doc_name: str) -> Path:
    """Return the absolute path to the input PDF.

    Checks DOC_PATH first (relative path inside input_files/, e.g.
    ``Taxation/Annulation et retaxation.pdf``).  Falls back to the flat
    ``<DOC_NAME>.pdf`` layout when DOC_PATH is absent.
    """
    input_files_root = project_root() / "data" / "input_files"
    doc_path = os.environ.get("DOC_PATH", "").strip()
    if doc_path:
        return input_files_root / doc_path
    return input_files_root / f"{doc_name}.pdf"


def _core_resolve_doc_name(args: argparse.Namespace, primary_flag: str) -> str:
    """Noyau : résout DOC_NAME, lève ConfigError — jamais SystemExit."""
    # 1. Explicit --doc-name wins immediately (no dotenv loading needed).
    doc_name = (getattr(args, "doc_name", None) or "").strip()
    if doc_name:
        return doc_name

    # 2. Load .env if provided (idempotent if already loaded).
    if getattr(args, "dotenv", None):
        _core_load_env(args.dotenv)

    # 3. Read from environment (may have been set by the .env just loaded).
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if doc_name:
        return doc_name

    raise ConfigError(
        f"Error: provide {primary_flag} <value>, or --dotenv <file> with DOC_NAME, "
        "or set the DOC_NAME variable in the environment."
    )


def resolve_doc_name(
    args: argparse.Namespace,
    *,
    primary_flag: str = "--input",
) -> str:
    """Return the document name (DOC_NAME) for the current run.

    Resolution order:
    1. ``args.doc_name`` — explicit ``--doc-name`` CLI argument.
    2. ``args.dotenv``   — load the .env file then read DOC_NAME from env.
    3. DOC_NAME already present in the process environment.

    Raises SystemExit with a user-friendly message when none of the above
    yield a non-empty value.

    Parameters
    ----------
    args:
        Parsed CLI namespace (from argparse).
    primary_flag:
        The flag name shown in the error message (e.g. ``"--input"`` or
        ``"--doc-name"``).  Used only for the error string.
    """
    try:
        return _core_resolve_doc_name(args, primary_flag)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
