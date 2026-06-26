"""
Stage 4 - Script de contrôle du Markdown généré par le VLM
Script 2 : markdown_control_vlm_modular.py

Ce script effectue une vérification approfondie du Markdown généré à partir des doctags enrichis,
en utilisant un VLM (Vision-Language Model) pour analyser chaque page du PDF original.

Chaque appel VLM reçoit uniquement le markdown de SA page (extrait depuis les doctags),
évitant les problèmes de duplication aux frontières de page et les hallucinations liées
à un contexte trop large.

Usage :
    uv run python markdown_control_vlm_modular.py --input doc.pdf --doctags doc.doctags
    uv run python markdown_control_vlm_modular.py --dotenv .env.test
    uv run python markdown_control_vlm_modular.py --input data/input_files/MonDoc.pdf
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.vlm_client import VlmConfig

import fitz  # PyMuPDF
import httpx

from utils.paths import project_root, load_env, resolve_doc_name, resolve_input_pdf

_log = logging.getLogger(__name__)


class _JsonFormatter(logging.Formatter):
    """Formatter JSON structuré pour l'agrégation de logs (Loki, sidecars Tekton, etc.)."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


_MAX_RETRIES = 3
_RETRY_DELAYS: tuple[int, ...] = (1, 2)  # seconds between attempts (len == _MAX_RETRIES - 1)
_DOCLING_TIMEOUT = 300  # max seconds for Docling doctags → markdown conversion before aborting


def _strip_code_fences(text: str) -> str:
    """Strip opening/closing code fences that Qwen sometimes wraps around its output.

    Handles ```json, ```markdown, ``` (bare), etc.
    """
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _should_retry(exc: Exception) -> bool:
    """Retourne True si l'erreur est transitoire et justifie un réessai."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


def _pdf_page_count(pdf_path: Path) -> int:
    """Retourne le nombre de pages du PDF (à appeler via asyncio.to_thread)."""
    with fitz.open(str(pdf_path)) as doc:
        return doc.page_count


# Logique métier
def pdf_page_to_base64(pdf_path: Path, page_num: int, dpi: int = 150) -> str:
    """
    Rend une page du PDF en image PNG encodée en base64.

    :param pdf_path: chemin vers le PDF
    :param page_num: numéro de page (1-based)
    :param dpi: résolution de rendu
    :return: image encodée en base64
    """
    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("png")
    return base64.b64encode(img_bytes).decode("utf-8")


PAGE_BREAK = "<!-- page-break -->"


def load_page_markdowns(md_path: Path) -> list[str]:
    """
    Charge le markdown paginé produit par stage 09 et retourne une liste,
    une entrée par page, en splitant sur le séparateur PAGE_BREAK.

    :param md_path: chemin vers le fichier .md produit par stage 09
    :return: liste de chaînes markdown, une par page
    """
    content = md_path.read_text(encoding="utf-8")
    pages = [p.strip() for p in content.split(PAGE_BREAK) if p.strip()]
    _log.info("Markdown: %d page(s) détectée(s) dans %s", len(pages), md_path.name)
    return pages


# Traitement des pages
async def process_page(
    page_num: int,
    total_pages: int,
    page_markdown: str,
    pdf_path: Path,
    semaphore: asyncio.Semaphore,
    client: httpx.AsyncClient,
    vlm_cfg: VlmConfig,
    prompt_template: str,
    dpi: int = 150,
    max_retries: int = _MAX_RETRIES,
    retry_delays: tuple[int, ...] = _RETRY_DELAYS,
) -> tuple[int, str]:
    """
    Traite une page : envoie son image + son markdown au VLM et récupère la correction.
    Tente jusqu'à max_retries fois sur erreur HTTP ou timeout transitoire.

    :param page_num: numéro de page (1-based)
    :param total_pages: nombre total de pages du PDF
    :param page_markdown: markdown de cette page uniquement (extrait depuis les doctags)
    :param pdf_path: chemin vers le PDF original
    :param semaphore: sémaphore de limitation de concurrence
    :param client: client HTTP partagé
    :param vlm_cfg: configuration VLM
    :param prompt_template: template du prompt à formater
    :param dpi: résolution de rendu de la page PDF
    :param max_retries: nombre maximum de tentatives VLM (injectable pour les tests)
    :param retry_delays: délais en secondes entre les tentatives (injectable pour les tests)
    :return: (numéro de page, markdown corrigé pour cette page)
    """
    from utils.vlm_client import call_vlm_async

    async with semaphore:
        _log.info("Traitement page %d/%d ...", page_num, total_pages)

        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num, dpi)
        except Exception as e:
            _log.exception("Erreur rendu PDF page %d : %s", page_num, e)
            return page_num, ""

        prompt = prompt_template.format(
            page_num=page_num,
            total_pages=total_pages,
            page_markdown=page_markdown,
        )

        for attempt in range(1, max_retries + 1):
            try:
                result = await call_vlm_async(client, vlm_cfg, image_b64, prompt)
                _log.info("Page %d/%d traitée.", page_num, total_pages)
                return page_num, _strip_code_fences(result)
            except (httpx.HTTPStatusError, httpx.TimeoutException) as e:
                if not _should_retry(e):
                    # 4xx client error: inutile de réessayer
                    _log.exception("Page %d : erreur client HTTP %s, pas de réessai.", page_num, e)
                    return page_num, ""
                if attempt == max_retries:
                    _log.exception("Page %d : échec après %d tentatives.", page_num, max_retries)
                    return page_num, ""
                delay = retry_delays[attempt - 1]
                _log.warning(
                    "Page %d : tentative %d/%d échouée (%s), réessai dans %ds...",
                    page_num, attempt, max_retries, e, delay,
                )
                await asyncio.sleep(delay)
            except Exception as e:
                _log.exception("Erreur VLM page %d : %s", page_num, e)
                return page_num, ""

        return page_num, ""  # jamais atteint, satisfait le type checker


# Pipeline principal
async def run(
    pdf_path: Path,
    output_path: Path,
    md_path: Path,
    max_workers: int = 1,
    dpi: int = 150,
    vlm_cfg: VlmConfig | None = None,
) -> None:
    """
    Traite le document page par page.

    Chaque appel VLM reçoit uniquement le markdown de SA page (issu du fichier .md
    produit par stage 09) + l'image de cette page.

    :param pdf_path: chemin vers le PDF original
    :param output_path: chemin de sortie pour le markdown vérifié par le VLM
    :param md_path: chemin vers le fichier .md paginé produit par stage 09
    :param max_workers: nombre de requêtes VLM simultanées
    :param dpi: résolution de rendu des pages PDF
    :param vlm_cfg: configuration VLM (injectable pour les tests ; sinon construite depuis l'environnement)
    """
    from utils.vlm_client import build_vlm_config, check_vlm_connectivity
    from prompts.prompts import VLM_PROMPT_STAGE4_CHECK_PAGE_EN

    if vlm_cfg is None:
        vlm_cfg = build_vlm_config()

    async with httpx.AsyncClient(verify=vlm_cfg.ca_path, timeout=180) as client:
        if not await check_vlm_connectivity(client, vlm_cfg):
            raise RuntimeError("VLM inaccessible, arrêt du pipeline.")
        _log.info("PDF      : %s", pdf_path)
        _log.info("Markdown : %s", md_path)
        _log.info("Sortie   : %s", output_path)
        _log.info("Workers  : %d", max_workers)
        _log.info("DPI      : %d", dpi)

        page_markdowns = load_page_markdowns(md_path)
        total_pages = len(page_markdowns)

        pdf_page_count = await asyncio.to_thread(_pdf_page_count, pdf_path)
        if pdf_page_count != total_pages:
            raise RuntimeError(
                f"Incohérence : {total_pages} page(s) dans le markdown mais "
                f"{pdf_page_count} page(s) dans le PDF ({pdf_path.name}). "
                f"Relancez stage 09 pour régénérer {md_path.name} avec les séparateurs <!-- page-break -->."
            )

        _log.info("%d page(s) à contrôler.", total_pages)

        semaphore = asyncio.Semaphore(max_workers)
        tasks = [
            process_page(
                page_num=p,
                total_pages=total_pages,
                page_markdown=page_markdowns[p - 1],
                pdf_path=pdf_path,
                semaphore=semaphore,
                client=client,
                vlm_cfg=vlm_cfg,
                prompt_template=VLM_PROMPT_STAGE4_CHECK_PAGE_EN,
                dpi=dpi,
            )
            for p in range(1, total_pages + 1)
        ]

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[tuple[int, str]] = []
        for page_num_idx, r in enumerate(raw_results, 1):
            if isinstance(r, BaseException):
                _log.error(
                    "Tâche page %d : exception inattendue.",
                    page_num_idx,
                    exc_info=r,
                )
                results.append((page_num_idx, ""))
            else:
                results.append(r)

    results_sorted = sorted(results, key=lambda x: x[0])
    failed_pages = [p for p, content in results_sorted if not content.strip()]
    page_corrections = [content for _, content in results_sorted if content.strip()]

    if failed_pages:
        _log.warning(
            "%d/%d page(s) échouée(s) non incluses dans la sortie : %s",
            len(failed_pages), total_pages, failed_pages,
        )

    if not page_corrections:
        raise RuntimeError(
            f"Toutes les pages ({total_pages}) ont échoué — "
            "fichier de sortie non sauvegardé. Vérifiez les logs VLM."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n\n".join(page_corrections), encoding="utf-8")
    _log.info("Markdown vérifié sauvegardé : %s", output_path)


# CLI
def parse_args() -> argparse.Namespace:
    """
    Analyse les arguments de la ligne de commande.

    :return: espace de noms avec les arguments parsés
    """
    parser = argparse.ArgumentParser(
        description=(
            "Contrôle qualité du Markdown généré par le VLM à partir des doctags. "
            "Envoie l'image de chaque page PDF + son markdown au VLM pour vérification. "
            "Produit un fichier Markdown corrigé."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python markdown_control_vlm_modular.py \\\n"
            "      --input data/input_files/MonDoc.pdf \\\n"
            "      --markdown data/output_files/MonDoc/MonDoc.md\n\n"
            "  # Chemins résolus automatiquement depuis le stem du PDF :\n"
            "  uv run python markdown_control_vlm_modular.py --input data/input_files/MonDoc.pdf\n\n"
            "  # Via variable d'environnement DOC_NAME :\n"
            "  uv run python markdown_control_vlm_modular.py --dotenv .env.test\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help=(
            "Chemin vers le PDF source. "
            "Si absent, résout data/input_files/<DOC_NAME>.pdf depuis l'environnement."
        ),
    )
    parser.add_argument(
        "--markdown", "-m",
        type=Path,
        default=None,
        help=(
            "Fichier Markdown paginé produit par stage 09. "
            "Défaut : data/output_files/<stem>/<stem>.md"
        ),
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Chemin du fichier Markdown de sortie. "
            "Défaut : data/output_files/<stem>/<stem>_vlm_check.md"
        ),
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=1,
        metavar="N",
        help="Nombre de requêtes VLM simultanées (1-10). Défaut : 1.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Résolution de rendu (DPI) des pages PDF envoyées au VLM. Défaut : 150.",
    )
    parser.add_argument(
        "--suffix", "-s",
        type=str,
        default="",
        metavar="SUFFIXE",
        help=(
            "Suffixe à ajouter au nom du fichier de sortie résolu automatiquement. "
            "Ex. : --suffix _v2 → <stem>_vlm_check_v2.md. "
            "Ignoré si --output est fourni."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de journalisation. Défaut : INFO.",
    )
    parser.add_argument(
        "--log-format",
        default="text",
        choices=["text", "json"],
        help=(
            "Format de journalisation : 'text' (lisible) ou 'json' (Loki / agrégation Tekton). "
            "Défaut : text."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger pour résoudre DOC_NAME (ex. : .env.test). Ignoré si --input est fourni.",
    )
    return parser.parse_args()


# Résolution des chemins
def resolve_pdf(args: argparse.Namespace) -> Path:
    """
    Résout le chemin du PDF selon la logique suivante :
    1. --input fourni → utilisé directement.
    2. Sinon → lit DOC_NAME depuis l'environnement (le dotenv est déjà chargé dans main()).

    :param args: arguments parsés
    :return: chemin absolu vers le PDF
    """
    if args.input:
        return args.input.resolve()
    doc_name = resolve_doc_name(args, primary_flag="--input")
    return resolve_input_pdf(doc_name)


def resolve_markdown(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Résout le chemin du fichier Markdown paginé produit par stage 09.
    Si --markdown est fourni, l'utilise directement.
    Sinon, construit le chemin par défaut : data/output_files/<stem>/<stem>_url_vlm.md

    :param args: arguments parsés
    :param pdf_path: chemin vers le PDF résolu
    :return: chemin absolu vers le fichier markdown source
    """
    if args.markdown:
        return args.markdown.resolve()
    stem = pdf_path.stem.strip()
    return project_root() / "data" / "output_files" / stem / f"{stem}_url_vlm.md"


def resolve_output(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Résout le chemin du fichier Markdown de sortie.
    Si --output est fourni, l'utilise directement.
    Sinon, construit le chemin par défaut : data/output_files/<stem>/<stem>_vlm_check<suffix>.md

    :param args: arguments parsés
    :param pdf_path: chemin vers le PDF résolu
    :return: chemin absolu vers le fichier de sortie
    """
    if args.output:
        return args.output.resolve()
    stem = pdf_path.stem.strip()
    suffix = getattr(args, "suffix", "")
    return project_root() / "data" / "output_files" / stem / f"{stem}_vlm_check{suffix}.md"


# Point d'entrée
def main() -> None:
    args = parse_args()

    _handler = logging.StreamHandler()
    _handler.setFormatter(
        _JsonFormatter()
        if args.log_format == "json"
        else logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )
    logging.basicConfig(level=getattr(logging, args.log_level), handlers=[_handler])

    if not 1 <= args.workers <= 10:
        raise SystemExit("Erreur : --workers doit être compris entre 1 et 10.")
    if not 72 <= args.dpi <= 600:
        raise SystemExit("Erreur : --dpi doit être compris entre 72 et 600.")

    if args.dotenv:
        load_env(args.dotenv)

    pdf_path = resolve_pdf(args)
    if not pdf_path.exists():
        raise SystemExit(f"Erreur : fichier PDF introuvable — {pdf_path}")

    md_path = resolve_markdown(args, pdf_path)
    if not md_path.exists():
        raise SystemExit(
            f"Erreur : fichier markdown introuvable — {md_path}\n"
            "Conseil : utilisez --markdown <chemin> pour spécifier le fichier d'entrée."
        )

    output_path = resolve_output(args, pdf_path)

    try:
        asyncio.run(run(
            pdf_path=pdf_path,
            output_path=output_path,
            md_path=md_path,
            max_workers=args.workers,
            dpi=args.dpi,
        ))
    except RuntimeError as e:
        _log.exception("%s", e)
        sys.exit(1)
    except Exception:
        _log.exception("Erreur inattendue lors du traitement.")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
