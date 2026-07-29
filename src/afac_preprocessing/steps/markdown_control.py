"""Étape markdown-control — contrôle qualité du Markdown via VLM, page par page.

Conversion du script ``simple_extraction/markdown_control_vlm.py`` (vague C).
Déjà async (``Semaphore`` + ``gather``) — patron de la vague D avec
url-tuning. Fonctions métier DÉPLACÉES telles quelles (invariant n°1) ; seul
le *dispatch* change (piège P7) : client partagé via ``ctx.vlm()``, plus
d'``asyncio.run()`` ni de ``client.close()`` dans l'étape. Le timeout 180 s
historique de cette étape est porté par le client du ClientBundle.

Chaque appel VLM reçoit uniquement le markdown de SA page + l'image de cette
page — évite les duplications aux frontières de page.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import fitz  # PyMuPDF

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed
from ..prompts.prompts import VLM_PROMPT_STAGE4_CHECK_PAGE_EN

if TYPE_CHECKING:
    from ..clients.base import AsyncVlmClient
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


# Fonctions métier — déplacées telles quelles
def _strip_code_fences(text: str) -> str:
    """Strip opening/closing code fences that Qwen sometimes wraps around its output.

    Handles ```json, ```markdown, ``` (bare), etc.
    """
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _pdf_page_count(pdf_path: Path) -> int:
    """Retourne le nombre de pages du PDF (à appeler via asyncio.to_thread)."""
    with fitz.open(str(pdf_path)) as doc:
        return doc.page_count


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
    vlm: "AsyncVlmClient",
    prompt_template: str,
    dpi: int = 150,
) -> tuple[int, str]:
    """
    Traite une page : envoie son image + son markdown au VLM et récupère la
    correction. Le retry sur erreur transitoire est géré par le client OpenAI.

    (Dispatch adapté au refactor : ``vlm`` est le AsyncVlmClient partagé du
    run. Corps inchangé.)

    :param page_num: numéro de page (1-based)
    :param total_pages: nombre total de pages du PDF
    :param page_markdown: markdown de cette page uniquement
    :param pdf_path: chemin vers le PDF original
    :param semaphore: sémaphore de limitation de concurrence
    :param prompt_template: template du prompt à formater
    :param dpi: résolution de rendu de la page PDF
    :return: (numéro de page, markdown corrigé pour cette page)
    """
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

        try:
            result = await vlm.vision_completion(prompt, image_b64)
            _log.info("Page %d/%d traitée.", page_num, total_pages)
            return page_num, _strip_code_fences(result)
        except Exception as e:
            _log.exception("Erreur VLM page %d : %s", page_num, e)
            return page_num, ""


class MarkdownControlStep(PipelineStep):
    """Vérifie/corrige le markdown page par page via VLM → _vlm_check.md."""

    name = "markdown-control"
    description = "Contrôle qualité du markdown VLM"
    requires_vlm = True

    def __init__(self, *, max_concurrency: int = 1, dpi: int = 150) -> None:
        # Mêmes défauts que --workers/--dpi du script historique.
        self.max_concurrency = max_concurrency
        self.dpi = dpi
        self.prompt_template = VLM_PROMPT_STAGE4_CHECK_PAGE_EN  # variante v2 (défaut pipeline)

    def inputs(self, ctx: "PipelineContext") -> list[Path]:
        return [ctx.workspace.source_pdf, ctx.workspace.url_vlm_markdown]

    def outputs(self, ctx: "PipelineContext") -> list[Path]:
        return [ctx.workspace.vlm_check_markdown]

    def execute(self, ctx: "PipelineContext") -> StepResult:
        return ctx.run_async(self._execute_async(ctx))  # ⚠ PAS asyncio.run() (P7)

    async def _execute_async(self, ctx: "PipelineContext") -> StepResult:
        ws = ctx.workspace
        pdf_path = ws.source_pdf
        md_path = ws.url_vlm_markdown
        output_path = ws.vlm_check_markdown
        vlm = ctx.vlm()

        if not await vlm.check_connectivity():
            raise StepFailed("VLM inaccessible, arrêt du pipeline.")
        _log.info("PDF      : %s", pdf_path)
        _log.info("Markdown : %s", md_path)
        _log.info("Sortie   : %s", output_path)
        _log.info("Workers  : %d", self.max_concurrency)
        _log.info("DPI      : %d", self.dpi)

        page_markdowns = load_page_markdowns(md_path)
        total_pages = len(page_markdowns)

        pdf_page_count = await asyncio.to_thread(_pdf_page_count, pdf_path)
        if pdf_page_count != total_pages:
            raise StepFailed(
                f"Incohérence : {total_pages} page(s) dans le markdown mais "
                f"{pdf_page_count} page(s) dans le PDF ({pdf_path.name}). "
                f"Relancez markdown-convert pour régénérer {md_path.name} avec les "
                "séparateurs <!-- page-break -->."
            )

        _log.info("%d page(s) à contrôler.", total_pages)

        semaphore = asyncio.Semaphore(self.max_concurrency)
        tasks = [
            process_page(
                page_num=p,
                total_pages=total_pages,
                page_markdown=page_markdowns[p - 1],
                pdf_path=pdf_path,
                semaphore=semaphore,
                vlm=vlm,
                prompt_template=self.prompt_template,
                dpi=self.dpi,
            )
            for p in range(1, total_pages + 1)
        ]

        # Pas de client.close() ici : le ClientBundle possède le client (P7).
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
            raise StepFailed(
                f"Toutes les pages ({total_pages}) ont échoué — "
                "fichier de sortie non sauvegardé. Vérifiez les logs VLM."
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n\n".join(page_corrections), encoding="utf-8")
        _log.info("Markdown vérifié sauvegardé : %s", output_path)
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
