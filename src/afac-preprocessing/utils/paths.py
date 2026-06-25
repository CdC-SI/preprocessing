"""Shared path and environment helpers for all pipeline scripts.

Replaces the copy-pasted _project_root() / PROJECT_ROOT constant and the
dotenv + DOC_NAME resolution that previously appeared ~12 times across the
pipeline scripts.
"""
import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_log = logging.getLogger(__name__)


def project_root() -> Path:
    """Return the afac-preprocessing project root (the directory that contains pyproject.toml).

    Respects the PROJECT_ROOT env variable so Tekton/container deployments can
    override the auto-detected path without rebuilding the image.
    """
    if "PROJECT_ROOT" in os.environ:
        return Path(os.environ["PROJECT_ROOT"]).resolve()
    # utils/paths.py lives at <root>/utils/paths.py — two levels up = <root>
    return Path(__file__).resolve().parent.parent


def load_env(dotenv_path: Path) -> None:
    """Load *dotenv_path* into the process environment.

    Raises SystemExit (not an exception) if the file is absent so the error
    message stays clean in CLI output.
    """
    resolved = dotenv_path.resolve()
    if not resolved.exists():
        raise SystemExit(f"Erreur : fichier .env introuvable — {resolved}")
    load_dotenv(dotenv_path=resolved)
    _log.info("Environnement chargé depuis : %s", resolved)


def resolve_input_pdf(doc_name: str) -> Path:
    """Return the absolute path to the input PDF.

    Checks DOC_PATH first (relative path inside input_files/, e.g.
    ``Taxation/Annulation et retaxation.pdf``).  Falls back to the flat
    ``<DOC_NAME>.pdf`` layout when DOC_PATH is absent.
    """
    doc_path = os.environ.get("DOC_PATH", "").strip()
    if doc_path:
        return project_root() / "data" / "input_files" / doc_path
    return project_root() / "data" / "input_files" / f"{doc_name}.pdf"


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
    # 1. Explicit --doc-name wins immediately (no dotenv loading needed).
    doc_name = (getattr(args, "doc_name", None) or "").strip()
    if doc_name:
        return doc_name

    # 2. Load .env if provided (idempotent if already loaded).
    if getattr(args, "dotenv", None):
        load_env(args.dotenv)

    # 3. Read from environment (may have been set by the .env just loaded).
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if doc_name:
        return doc_name

    raise SystemExit(
        f"Erreur : fournir {primary_flag} <valeur>, ou --dotenv <fichier> avec DOC_NAME, "
        "ou définir la variable DOC_NAME dans l'environnement."
    )
