"""
Stage 4 - Script de contrôle du Markdown généré par le VLM
Script 2 : markdown_control_vlm.py

Ce script effectue une vérification approfondie du Markdown généré à partir des doctags enrichis,
en utilisant un VLM (Vision-Language Model) pour analyser chaque page du PDF original.
"""
import asyncio
import base64
import logging
import os
import sys
from pathlib import Path
import fitz  # PyMuPDF
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prompts.prompts import VLM_PROMPT_STAGE4_CHECK_EN
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
            data = resp.json()
            _log.info("VLM accessible. Réponse : %s", data["choices"][0]["message"]["content"].strip())
            return True
    except Exception as e:
        _log.exception("Impossible de joindre le VLM : %s", e)
        return False


async def call_vlm_async(image_b64: str, prompt: str) -> str:
    """
    Envoie une page PDF (image) + le prompt au VLM et retourne la correction pour cette page.

    :param image_b64: image de la page encodée en base64
    :param prompt: prompt incluant le markdown complet et le numéro de page
    :return: markdown corrigé pour cette page uniquement
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
        "temperature": 0.0,  # Valeur Qwen par défaut
    }
    async with httpx.AsyncClient(verify=CA_PATH, timeout=180) as client:
        resp = await client.post(VLM_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def process_page(
    page_num: int,
    total_pages: int,
    full_markdown: str,
    pdf_path: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[int, str]:
    """
    Traite une page : envoie son image + le markdown complet au VLM,
    récupère le markdown corrigé pour cette page uniquement.

    :param page_num: numéro de page (1-based)
    :param total_pages: nombre total de pages du PDF
    :param full_markdown: markdown complet du document (contexte pour le VLM)
    :param pdf_path: chemin vers le PDF original
    :param semaphore: sémaphore de limitation de concurrence
    :return: (numéro de page, markdown corrigé pour cette page)
    """
    async with semaphore:
        _log.info("Traitement page %d/%d ...", page_num, total_pages)
        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num)
            prompt = VLM_PROMPT_STAGE4_CHECK_EN.format(
                page_num=page_num,
                total_pages=total_pages,
                full_markdown=full_markdown,
            )
            result = await call_vlm_async(image_b64, prompt)
            _log.info("Page %d/%d traitée.", page_num, total_pages)
            return page_num, result
        except Exception as e:
            _log.exception("Erreur page %d : %s", page_num, e)
            return page_num, ""


async def run(pdf_path: Path, md_path: Path, output_path: Path) -> None:
    """
    Traite le document page par page : chaque appel VLM reçoit le markdown complet
    + l'image d'une page, et retourne la correction pour cette page uniquement.
    Les corrections sont ensuite assemblées en un seul markdown final.

    :param pdf_path: chemin vers le PDF original
    :param md_path: chemin vers le markdown généré par le pipeline stage4
    :param output_path: chemin de sortie pour le markdown vérifié par le VLM
    """
    if not await check_vlm_connectivity():
        raise RuntimeError("VLM inaccessible, arrêt du pipeline.")

    print(f"\n{'='*60}")
    print(f"PDF      : {pdf_path.name}")
    print(f"Markdown : {md_path.name}")
    print(f"Sortie   : {output_path.name}")
    print(f"{'='*60}\n")

    full_markdown = md_path.read_text(encoding="utf-8")

    with fitz.open(str(pdf_path)) as doc:
        total_pages = doc.page_count
    print(f"{total_pages} page(s) à contrôler.\n")

    semaphore = asyncio.Semaphore(MAX_WORKERS)
    tasks = [
        process_page(
            page_num=p,
            total_pages=total_pages,
            full_markdown=full_markdown,
            pdf_path=pdf_path,
            semaphore=semaphore,
        )
        for p in range(1, total_pages + 1)
    ]

    results = await asyncio.gather(*tasks)

    # Assemble les corrections de chaque page dans l'ordre pour former un seul markdown final
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
    md_path = PROJECT_ROOT / "data" / "output_files" / "stage4_test" / f"{DOC_NAME}{GEN_ID}.md"
    output_path = PROJECT_ROOT / "data" / "output_files" / "stage4_test" / f"{DOC_NAME}{GEN_ID}_vlm_check.md"

    asyncio.run(run(pdf_path, md_path, output_path))
