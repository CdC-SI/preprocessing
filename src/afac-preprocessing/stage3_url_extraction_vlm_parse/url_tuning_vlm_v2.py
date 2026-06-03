# Ce script utilise le VLM Qwen 3.5 pour analyser les URL du fichier JSONL et les faire matcher avec ler texteau format markdown [text](myurl)
# Il faut également envoyer le texte original pour que le VLM puisse faire le lien entre les URL et les textes d'ancrage
# le vlm corrige également les textes d'ancrage pour les rendre plus propres et normalisés (ex: enlever les espaces superflus, corriger les apostrophes, etc.) par rapport au pdf de base

# utiliser la librairie asyncio pour faire du parallélisme et accélérer le processus

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

from prompts.prompts import VLM_PROMPT_CORRECTION_STAGE_3_TEST_v2 # charge le prompt depuis le fichier prompts.py du dossier prompts
from utils.config import load_vlm_config

config = load_vlm_config()
CA_PATH = config["CA_PATH"]
VLM_URL = config["VLM_URL"]
VLM_MODEL_NAME = config["VLM_MODEL_NAME"]
_log = logging.getLogger(__name__)

MAX_WORKERS = 1

# Appel direct au VLM avec httpx (async)
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
        # "temperature": 0.0, # température à 0 pour des réponses plus déterministes, important pour la correction de texte et l'extraction d'URL
    }
    async with httpx.AsyncClient(verify=CA_PATH, timeout=120) as client:
        resp = await client.post(VLM_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

# Test de connectivité
# A remplacer en tapant dans le endpoint KIERAN 
async def check_vlm_connectivity() -> bool:
    #Teste la connectivité au VLM en envoyant un prompt minimal.
    try:
        _log.info("Test de connectivité au VLM : %s ...", VLM_URL)
        payload = {
            "model": VLM_MODEL_NAME,
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": "ping"}],
            }],
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

# Helpers
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
    # Retourne les liens d'une page donnée (1-indexed).
    return [l for l in links if l.get("page_number") == page]

def pdf_page_to_base64(pdf_path: Path, page_num: int) -> str:
    # Convertit une page PDF en image base64 pour le VLM.
    doc = fitz.open(str(pdf_path))
    page = doc[page_num - 1] # -1 car fitz utilise un index 0-based
    pix = page.get_pixmap(dpi = 150) # 150 dpi pour Qwen 3.5
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

    return VLM_PROMPT_CORRECTION_STAGE_3_TEST_v2.format(
        page_tags = page_tags, 
        links_str = links_str,
        )

# Appel VLM pour chaque page, avec gestion de la concurrence via un sémaphore pour limiter le nombre de requêtes simultanées
async def process_page(
    page_num: int,
    page_tags: str,
    page_links: list[dict],
    pdf_path: Path,
    semaphore: asyncio.Semaphore, # Semaphore pour limiter le nombre de requêtes simultanées au VLM
) -> tuple[int, str]:
    async with semaphore:
        _log.info(f"Page {page_num} : {len(page_links)} lien(s) à insérer...")
        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num) # Convertit la page PDF en image base64 dans un thread séparé pour ne pas bloquer l'event loop
            prompt = build_prompt(page_tags, page_links)
            result = await call_vlm_async(prompt, image_b64)
            _log.info(f"Page {page_num} traitée.")
            return page_num, result
        except Exception as e:
            logging.exception(f"Erreur page {page_num} : {e}")
            return page_num, page_tags  # fallback : retourne le texte original

# Split / Rebuild doctags

def split_doctags_by_page(doctags: str, n_pages: int) -> dict[int, str]:
    # Divise le doctags en sections par page en utilisant les balises <page_break>.
    # Si aucun <page_break> n'est trouvé, fallback sur la distribution par count.
    # Split sur les balises <page_break> insérées par Docling
    parts = re.split(r'<page_break\s*/?>', doctags) # le regex 

    if len(parts) > 1:
        _log.info("Split par <page_break> : %d page(s) détectées.", len(parts))
        pages = {}
        for i, part in enumerate(parts):
            content = part.strip()
            if content:
                pages[i + 1] = content
        return pages

    # Fallback : distribution par count (si pas de <page_break>)
    _log.warning("Aucun <page_break> trouvé, fallback distribution par count.")
    pattern = re.compile(
        r'(<(?!/)(?!doctag)\w+>'            # Le regex commence par une balise ouvrante qui n'est pas <doctag> ni une balise fermante
        r'(?:<loc_\d+>)*'                   # le regex autorise ensuite des balises <loc_N> optionnelles qui peuvent se répéter, pour capturer les lignes avec ou sans coordonnées
        r'.*?'                              # le regex capture ensuite tout le contenu de la ligne, y compris les balises imbriquées pour s'arrêter à la fin de la ligne
        r'(?=<(?!loc_)\w+>|</doctag>|$))',  # le regex s'assure que la ligne capturée est suivie d'une nouvelle balise ouvrante (qui n'est pas une balise de coordonnées <loc_N>), ou d'une balise fermante </doctag>, ou de la fin du texte, pour éviter de capturer plusieurs lignes d'un coup en cas de balises imbriquées
        re.DOTALL                           # DOTALL pour que le . capture aussi les sauts de ligne, au cas où une ligne serait découpée en plusieurs lignes dans le doctags
    )
    all_tags = [tag.strip() for tag in pattern.findall(doctags) if tag.strip()]
    tags_per_page = max(1, len(all_tags) // n_pages)

    pages = {}
    for i in range(n_pages):
        start = i * tags_per_page
        end = start + tags_per_page if i < n_pages - 1 else len(all_tags)
        pages[i + 1] = "\n".join(all_tags[start:end])
    return pages

def rebuild_doctags(header: str, pages: dict[int, str]) -> str:
    # Reconstruit le doctags complet depuis les pages traitées, sans lignes vides en excès.
    lines = []
    for p in sorted(pages):
        content = pages[p].strip()
        if content:
            # Supprime les lignes vides multiples dans le contenu retourné par le VLM
            content = re.sub(r'\n{2,}', '\n', content)
            lines.append(content)
    body = "\n".join(lines)
    return f"{header}\n{body}\n</doctag>"

# Pipeline principal
def extract_header(doctags: str) -> str:
    # Extrait l'en-tête du doctags (avant la première balise).
    m = re.match(r'^(.*?)(?=<(?!doctag)\w+>)', doctags, re.DOTALL) # Ne pas changer car le VLM perd la structure après. Ne pas écouter SONARQUBE 
    return m.group(1).strip() if m else "<doctag>"

async def run(pdf_path: Path, doctags_path: Path, jsonl_path: Path, output_path: Path):
    # Test de connectivité AVANT de lancer le pipeline
    if not await check_vlm_connectivity():
        raise RuntimeError("VLM inaccessible, arrêt du pipeline.")
    print(f"\n{'='*60}")
    print(f"PDF : {pdf_path.name}")
    print(f"Doctags : {doctags_path.name}")
    print(f"JSONL : {jsonl_path.name}")
    print(f"{'='*60}\n")

    # Chargement
    doctags = load_doctags(doctags_path)
    links = load_jsonl_links(jsonl_path)

    # Nombre de pages du PDF
    doc = fitz.open(str(pdf_path))
    n_pages = doc.page_count
    doc.close()
    print(f"{n_pages} page(s) détectées, {len(links)} lien(s) au total.\n")

    # Split doctags par page
    pages_tags = split_doctags_by_page(doctags, n_pages)
    header = extract_header(doctags)

    # Traitement concurrent page par page
    semaphore = asyncio.Semaphore(MAX_WORKERS)
    tasks = [
        process_page(
            page_num = p,
            page_tags = pages_tags.get(p, ""),
            page_links = get_links_for_page(links, p),
            pdf_path = pdf_path,
            semaphore = semaphore,
        )
        for p in range(1, n_pages + 1)
        if pages_tags.get(p, "").strip()
    ]

    results = await asyncio.gather(*tasks)

    # Reconstruction du doctags final
    processed_pages = dict(sorted(results))
    final_doctags = rebuild_doctags(header, processed_pages)

    # Sauvegarde
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_doctags, encoding="utf-8")
    print(f"\nDoctags enrichi sauvegardé : {output_path}")

if __name__ == "__main__":
    BASE = PROJECT_ROOT
    DATA = BASE / "data" / "output_files" / "stage3_test"

    # À adapter selon ton document
    DOC_NAME = os.environ.get("DOC_NAME", "")
    if not DOC_NAME:
        raise RuntimeError("DOC_NAME not set. Please define it in your .env.test.")

    pdf_path = BASE / "data" / "input_files" / f"{DOC_NAME}.pdf"
    doctags_path = BASE / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures.doctags"
    jsonl_path = DATA / DOC_NAME / f"hyperlinks_data_{DOC_NAME}.jsonl"
    output_path = DATA / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures_url_vlm.doctags"

    asyncio.run(run(pdf_path, doctags_path, jsonl_path, output_path))