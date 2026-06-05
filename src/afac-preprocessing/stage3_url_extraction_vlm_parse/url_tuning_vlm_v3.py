# Ce script utilise le VLM Qwen 3.5 pour analyser les URL du fichier JSONL et les faire matcher avec leur texte au format markdown [text](myurl)
# Le VLM reconstruit entièrement le doctags page par page (en utilisant le doctags original comme support + l'image PDF + les liens)
# Les résultats sont concaténés en un seul fichier doctags final

import os
import logging
import asyncio
import json
import re
import sys
from pathlib import Path
import fitz  # PyMuPDF
import base64
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prompts.prompts import VLM_PROMPT_CORRECTION_STAGE_3_TEST_enhance
from utils.config import load_vlm_config

config = load_vlm_config()
CA_PATH = config["CA_PATH"]
VLM_URL = config["VLM_URL"]
VLM_MODEL_NAME = config["VLM_MODEL_NAME"]
_log = logging.getLogger(__name__)

MAX_WORKERS = 1

async def call_vlm_async(prompt: str, image_b64: str) -> str:
    payload = {
        "model": VLM_MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 8192, # A modifier si necessaire
        "temperature": 0.0, # température à 0 pour des réponses plus déterministes, important pour la correction de texte et l'extraction d'URL
   
    }
    async with httpx.AsyncClient(verify=CA_PATH, timeout=120) as client:
        resp = await client.post(VLM_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def check_vlm_connectivity() -> bool:
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
        logging.exception("Impossible de joindre le VLM : %s", e)
        return False

def load_doctags(doctags_path: Path) -> str:
    return doctags_path.read_text(encoding="utf-8")

def load_jsonl_links(jsonl_path: Path) -> list[dict]:
    links = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                links.append(json.loads(line))
    return links

def get_links_for_page(links: list[dict], page: int) -> list[dict]:
    return [l for l in links if l.get("page_number") == page]

def pdf_page_to_base64(pdf_path: Path, page_num: int) -> str:
    doc = fitz.open(str(pdf_path))
    page = doc[page_num - 1]
    pix = page.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")
    doc.close()
    return base64.b64encode(img_bytes).decode("utf-8")

def build_prompt(page_tags: str, page_links: list[dict]) -> str:
    links_str = "\n".join(
        f'{i+1}. texte: "{l["text"]}" -> url: {l["hyperlink"]}'
        for i, l in enumerate(page_links)
    )
    if not links_str:
        links_str = "Aucune URL pour cette page."

    return VLM_PROMPT_CORRECTION_STAGE_3_TEST_enhance.format(
        page_tags=page_tags,
        links_str=links_str,
    )

async def process_page(
    page_num: int,
    page_tags: str,
    page_links: list[dict],
    pdf_path: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[int, str]:
    async with semaphore:
        _log.info(f"Page {page_num} : {len(page_links)} lien(s) à insérer...")
        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num)
            prompt = build_prompt(page_tags, page_links)
            result = await call_vlm_async(prompt, image_b64)
            _log.info(f"Page {page_num} traitée.")
            return page_num, result
        except Exception as e:
            logging.exception(f"Erreur page {page_num} : {e}")
            return page_num, page_tags  # fallback : retourne le doctags original de la page

def split_doctags_by_page(doctags: str, n_pages: int) -> dict[int, str]:
    parts = re.split(r'<page_break\s*/?>', doctags)

    if len(parts) > 1:
        _log.info("Split par <page_break> : %d page(s) détectées.", len(parts))
        pages = {}
        for i, part in enumerate(parts):
            content = part.strip()
            if content:
                pages[i + 1] = content
        return pages

    _log.warning("Aucun <page_break> trouvé, fallback distribution par count.")
    pattern = re.compile(
        r'(<(?!/)(?!doctag)\w+>'
        r'(?:<loc_\d+>)*'
        r'.*?'
        r'(?=<(?!loc_)\w+>|</doctag>|$))',
        re.DOTALL
    )
    all_tags = [tag.strip() for tag in pattern.findall(doctags) if tag.strip()]
    tags_per_page = max(1, len(all_tags) // n_pages)

    pages = {}
    for i in range(n_pages):
        start = i * tags_per_page
        end = start + tags_per_page if i < n_pages - 1 else len(all_tags)
        pages[i + 1] = "\n".join(all_tags[start:end])
    return pages

def assemble_doctags(pages: dict[int, str]) -> str:
    """Assemble les contenus doctags de chaque page produits par le VLM en un seul fichier doctags.
    On nettoie ici les balises <doctag> / </doctag> / </doctags> renvoyées par le VLM pour éviter les doublons
    ou typos, et on wrappe tout proprement avec une seule paire <doctag>...</doctag>.
    """
    def _strip_doctag_tags(s: str) -> str:
        # retire toutes les variantes d'ouverture/fermeture doctag (ex: <doctag>, </doctag>, </doctags>)
        s = re.sub(r'</?doctag[s]?>', '', s, flags=re.IGNORECASE)
        # retire balises vides / header éventuels renvoyés
        s = s.strip()
        return s

    parts = []
    for page_num in sorted(pages):
        content = pages[page_num].strip()
        if not content:
            continue
        content = _strip_doctag_tags(content)
        # collapse lignes vides multiples
        content = re.sub(r'\n{2,}', '\n', content)
        if content:
            parts.append(content)
    body = "\n".join(parts).strip()
    # assure une seule paire de balises en sortie
    return f"<doctag>\n{body}\n</doctag>"

async def run(pdf_path: Path, doctags_path: Path, jsonl_path: Path, output_path: Path):
    if not await check_vlm_connectivity():
        raise RuntimeError("VLM inaccessible, arrêt du pipeline.")

    print(f"\n{'='*60}")
    print(f"PDF      : {pdf_path.name}")
    print(f"Doctags  : {doctags_path.name}")
    print(f"JSONL    : {jsonl_path.name}")
    print(f"{'='*60}\n")

    doctags = load_doctags(doctags_path)
    links = load_jsonl_links(jsonl_path)

    doc = fitz.open(str(pdf_path))
    n_pages = doc.page_count
    doc.close()
    print(f"{n_pages} page(s) détectées, {len(links)} lien(s) au total.\n")

    pages_tags = split_doctags_by_page(doctags, n_pages)

    semaphore = asyncio.Semaphore(MAX_WORKERS)
    tasks = [
        process_page(
            page_num=p,
            page_tags=pages_tags.get(p, ""),
            page_links=get_links_for_page(links, p),
            pdf_path=pdf_path,
            semaphore=semaphore,
        )
        for p in range(1, n_pages + 1)
        if pages_tags.get(p, "").strip()
    ]

    results = await asyncio.gather(*tasks)

    # Le VLM a produit le contenu doctags de chaque page → on assemble tout
    processed_pages = dict(sorted(results))
    final_doctags = assemble_doctags(processed_pages)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_doctags, encoding="utf-8")
    print(f"\nDoctags final sauvegardé : {output_path}")

if __name__ == "__main__":
    BASE = PROJECT_ROOT
    DATA = BASE / "data" / "output_files" / "stage3_test"

    GEN_ID = os.environ.get("GEN_ID", "")
    DOC_NAME = os.environ.get("DOC_NAME", "")
    if not DOC_NAME:
        raise RuntimeError("DOC_NAME not set. Please define it in your .env.test.")

    pdf_path = BASE / "data" / "input_files" / f"{DOC_NAME}.pdf"
    doctags_path = BASE / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures.doctags"
    jsonl_path = DATA / DOC_NAME / f"hyperlinks_data_{DOC_NAME}.jsonl"
    output_path = DATA / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures_url_vlm{GEN_ID}.doctags"

    asyncio.run(run(pdf_path, doctags_path, jsonl_path, output_path))