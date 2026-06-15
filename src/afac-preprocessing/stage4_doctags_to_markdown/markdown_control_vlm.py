"""
Stage 4 - Script de contrôle du Markdown généré par le VLM
Script 2 : markdown_control_vlm.py

Ce script effectue une vérification approfondie du Markdown généré à partir des doctags enrichis,
en utilisant un VLM (Vision-Language Model) pour analyser chaque page du PDF original.

Chaque appel VLM reçoit uniquement le markdown de SA page (extrait depuis les doctags),
évitant les problèmes de duplication aux frontières de page et les hallucinations liées
à un contexte trop large.
"""
import asyncio
import base64
import logging
import os
import re
import sys
from pathlib import Path
import fitz  # PyMuPDF
import httpx
from docling_core.types.doc.document import DocTagsDocument, DoclingDocument

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prompts.prompts import VLM_PROMPT_STAGE4_CHECK_PAGE_EN
from utils.config import load_vlm_config

config = load_vlm_config()
CA_PATH = config["CA_PATH"]
VLM_URL = config["VLM_URL"]
VLM_MODEL_NAME = config["VLM_MODEL_NAME"]

_log = logging.getLogger(__name__)

MAX_WORKERS = 1  # requêtes simultanées au VLM


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


def _apply_markdown_transforms(text: str) -> str:
    """Apply the same post-processing transforms as convert_doctags_to_markdown.
    """
    text = re.sub(
        r'\[\[COLOR:([^\]]+)\]\](.*?)\[\[/COLOR\]\]',
        lambda m: f'<span style="color:{m.group(1)}">{m.group(2)}</span>',
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r'\\_\\_(.*?)\\_\\_',
        lambda m: f'<u>{m.group(1)}</u>',
        text,
        flags=re.DOTALL,
    )
    return text


def build_page_markdowns(doctags_path: Path) -> list[str]:
    """
    Convertit chaque page du fichier doctags en markdown indépendant.

    Chaque bloc <doctag>...</doctag> correspond à une page. Les convertir
    individuellement élimine l'ambiguïté des frontières de page que Docling
    ne conserve pas dans l'export markdown global.

    :param doctags_path: chemin vers le fichier .doctags multi-pages
    :return: liste de chaînes markdown, une par page
    """
    from stage4_doctags_to_markdown.convert_doctags_to_markdown import (
        _hoist_misplaced_tags,
        _split_pages,
    )

    content = doctags_path.read_text(encoding="utf-8")
    content = _split_pages(content)
    content = _hoist_misplaced_tags(content)

    page_blocks = re.findall(r"<doctag>(.*?)</doctag>", content, re.DOTALL)
    _log.info("Doctags: %d page(s) détectée(s)", len(page_blocks))

    page_markdowns = []
    for i, block in enumerate(page_blocks, 1):
        single = f"<doctag>{block}</doctag>"
        dt = DocTagsDocument.from_multipage_doctags_and_images(single, None)
        doc = DoclingDocument.load_from_doctags(dt)
        md = doc.export_to_markdown()
        md = _apply_markdown_transforms(md)
        page_markdowns.append(md.strip())
        _log.debug("Page %d/%d convertie (%d chars)", i, len(page_blocks), len(md))

    return page_markdowns


async def check_vlm_connectivity() -> bool:
    """
    Vérifie que le VLM est accessible avant de lancer le traitement.

    :return: True si accessible, False sinon
    """
    try:
        _log.info("Test de connectivité au VLM : %s ...", VLM_URL)
        payload = {
            "model": VLM_MODEL_NAME,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
            "max_tokens": 10,
        }
        async with httpx.AsyncClient(verify=CA_PATH, timeout=30) as client:
            resp = await client.post(VLM_URL, json=payload)
            resp.raise_for_status()
            _log.info("VLM accessible. HTTP %s", resp.status_code)
            return True
    except Exception as e:
        _log.exception("Impossible de joindre le VLM : %s", e)
        return False


async def call_vlm_async(image_b64: str, prompt: str) -> str:
    """
    Envoie une page PDF (image) + le prompt au VLM et retourne la correction.

    :param image_b64: image de la page encodée en base64
    :param prompt: prompt incluant le markdown de la page et le numéro de page
    :return: markdown corrigé pour cette page
    """
    payload = {
        "model": VLM_MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 8192,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},  # désactive le mode thinking Qwen3 (évite content=null)
    }
    async with httpx.AsyncClient(verify=CA_PATH, timeout=180) as client:
        resp = await client.post(VLM_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        content = message.get("content")
        if content is not None:
            return content.strip()
        _log.warning("content=null, full message: %s", message)
        raise ValueError(f"VLM returned null content. Full message: {message}")


async def process_page(
    page_num: int,
    total_pages: int,
    page_markdown: str,
    pdf_path: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[int, str]:
    """
    Traite une page : envoie son image + son markdown au VLM et récupère la correction.

    :param page_num: numéro de page (1-based)
    :param total_pages: nombre total de pages du PDF
    :param page_markdown: markdown de cette page uniquement (extrait depuis les doctags)
    :param pdf_path: chemin vers le PDF original
    :param semaphore: sémaphore de limitation de concurrence
    :return: (numéro de page, markdown corrigé pour cette page)
    """
    async with semaphore:
        _log.info("Traitement page %d/%d ...", page_num, total_pages)
        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num)
            prompt = VLM_PROMPT_STAGE4_CHECK_PAGE_EN.format(
                page_num=page_num,
                total_pages=total_pages,
                page_markdown=page_markdown,
            )
            result = await call_vlm_async(image_b64, prompt)
            _log.info("Page %d/%d traitée.", page_num, total_pages)
            return page_num, result
        except Exception as e:
            _log.exception("Erreur page %d : %s", page_num, e)
            return page_num, ""


async def run(pdf_path: Path, output_path: Path, doctags_path: Path) -> None:
    """
    Traite le document page par page.

    Chaque appel VLM reçoit uniquement le markdown de SA page (extrait depuis les doctags)
    + l'image de cette page. Cela évite les duplications aux frontières de page et les
    hallucinations provenant du contexte global.

    :param pdf_path: chemin vers le PDF original
    :param output_path: chemin de sortie pour le markdown vérifié par le VLM
    :param doctags_path: chemin vers le fichier .doctags source (pour la découpe par page)
    """
    if not await check_vlm_connectivity():
        raise RuntimeError("VLM inaccessible, arrêt du pipeline.")

    print(f"\n{'='*60}")
    print(f"PDF      : {pdf_path.name}")
    print(f"Doctags  : {doctags_path.name}")
    print(f"Sortie   : {output_path.name}")
    print(f"{'='*60}\n")

    page_markdowns = await asyncio.to_thread(build_page_markdowns, doctags_path)
    total_pages = len(page_markdowns)

    with fitz.open(str(pdf_path)) as doc:
        pdf_page_count = doc.page_count
    if pdf_page_count != total_pages:
        raise RuntimeError(
            f"Incohérence : {total_pages} page(s) dans les doctags mais "
            f"{pdf_page_count} page(s) dans le PDF ({pdf_path.name}). "
            "Vérifiez que le PDF et les doctags correspondent au même document."
        )

    print(f"{total_pages} page(s) à contrôler.\n")

    semaphore = asyncio.Semaphore(MAX_WORKERS)
    tasks = [
        process_page(
            page_num=p,
            total_pages=total_pages,
            page_markdown=page_markdowns[p - 1],
            pdf_path=pdf_path,
            semaphore=semaphore,
        )
        for p in range(1, total_pages + 1)
    ]

    results = await asyncio.gather(*tasks)

    page_corrections = [content for _, content in sorted(results) if content.strip()]
    final_markdown = "\n\n".join(page_corrections)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_markdown, encoding="utf-8")
    print(f"\nMarkdown vérifié sauvegardé : {output_path}")


if __name__ == "__main__":
    GEN_ID = os.environ.get("GEN_ID", "")
    DOC_NAME = os.environ.get("DOC_NAME", "")
    if not DOC_NAME:
        raise RuntimeError("DOC_NAME not set. Please define it in your .env.test.")

    pdf_path = PROJECT_ROOT / "data" / "input_files" / f"{DOC_NAME}.pdf"
    doctags_path = (
        PROJECT_ROOT / "data" / "output_files" / "stage3_test"
        / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures_url_vlm.doctags"
    )
    output_path = PROJECT_ROOT / "data" / "output_files" / "stage4_test" / f"{DOC_NAME}{GEN_ID}_vlm_check.md"

    asyncio.run(run(pdf_path, output_path, doctags_path))
