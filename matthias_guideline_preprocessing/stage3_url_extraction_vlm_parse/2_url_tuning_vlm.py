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
import certifi
from dotenv import load_dotenv
import fitz  # PyMuPDF
import base64
import httpx

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

# Config
# Environnement & certificats
# Chargement de .env.test

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger(__name__)

dotenv_path = Path(__file__).resolve().parent.parent / ".env.test"
_log.info("Loading dotenv from: %s (exists: %s)", dotenv_path.resolve(), dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

# Même logique SSL que description_image_context.py
custom_ca = os.environ.get("VLM_CA_PEM")
CA_PATH = custom_ca if custom_ca and Path(custom_ca).exists() else certifi.where()
_log.info("CA bundle : %s", CA_PATH)

VLM_URL = os.environ.get("VLM_URL", "")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")
if not VLM_URL:
    raise RuntimeError(
        f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL."
    )
_log.info("VLM_URL : %s", VLM_URL)
_log.info("VLM_MODEL_NAME : %s", VLM_MODEL_NAME)

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
        "max_tokens": 4096,
    }
    async with httpx.AsyncClient(verify=CA_PATH, timeout=120) as client:
        resp = await client.post(VLM_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

# Test de connectivité
async def check_vlm_connectivity() -> bool:
    """Teste la connectivité au VLM en envoyant un prompt minimal."""
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
    """Retourne les liens d'une page donnée (1-indexed)."""
    return [l for l in links if l.get("page_number") == page]

def pdf_page_to_base64(pdf_path: Path, page_num: int) -> str:
    """Convertit une page PDF en image base64 pour le VLM."""
    doc  = fitz.open(str(pdf_path))
    page = doc[page_num - 1]
    pix  = page.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")
    doc.close()
    return base64.b64encode(img_bytes).decode("utf-8")

def build_prompt(page_tags: str, page_links: list[dict]) -> str:
    links_str = "\n".join(
        f'{i+1}. texte: "{l["text"]}" → url: {l["hyperlink"]}'
        for i, l in enumerate(page_links)
    )
    return rf"""
Tu es un assistant spécialisé dans la correction et l'enrichissement de fichiers doctags.

Tu reçois :
1. Le contenu doctags d'UNE page (balises doctags avec coordonnées <loc_X>)
2. Une liste numérotée d'URLs à insérer avec leur texte d'ancrage
3. L'image de la page PDF originale pour référence visuelle

## Étape 1 : Correction OCR
Corrige UNIQUEMENT les erreurs évidentes dans le texte des balises :
- Apostrophes typographiques → remplace par '
- Accents manquants ou incorrects
- Espaces superflus ou mots coupés
- Tirets mal encodés (–, —) → remplace par -
- Supprimer les caractères parasite comme : , ou remplace les par celui correspondant s'il est identifiable
GARDE EXACTEMENT la structure doctags et les coordonnées <loc_X> intactes.

## Étape 2 : Insertion des URLs (OBLIGATOIRE)
Pour CHAQUE URL de la liste numérotée ci-dessous, tu DOIS l'insérer dans le doctags :

RÈGLES STRICTES :
1. Trouve le texte d'ancrage dans les balises (la correspondance peut être approximative)
2. Si le texte d'ancrage = contenu entier de la balise → remplace tout le contenu par [texte](url)
   Exemple: <text><loc_60><loc_168><loc_324><loc_173>Process bpanda</text>
   Devient: <text><loc_60><loc_168><loc_324><loc_173>[Process bpanda](https://...)</text>
3. Si le texte d'ancrage est une sous-partie → remplace uniquement cette sous-partie
   Exemple: <text><loc_60><loc_314>Il faut voir art. 1 al 1 LAVS pour...</text>
   Devient: <text><loc_60><loc_314>Il faut voir [art. 1 al 1 LAVS](https://...) pour...</text>
4. Si le texte n'est pas trouvé → ajoute [texte](url) à la fin du contenu de la balise la plus proche
5. Ne modifie JAMAIS les balises doctags (<text>, <list_item>, etc.) ni les coordonnées <loc_X>

## RÈGLE ABSOLUE (structure) :
- Tu dois restituer **TOUTES** les balises doctags présentes dans le contenu doctags d'origine, même celles que tu ne modifies pas.
- Ne supprime, ne fusionne, ni ne réordonne aucune balise.
- Chaque balise d'entrée doit exister dans la sortie, même si son contenu n'est pas modifié.
- Si une balise contient des bracket avec du texte, tu dois les corriger et les enrichir, mais la balise elle-même doit rester inchangée, par exemple:
    - "Version": "4.0", "Date": "13.11.2024", "Description, Remarques": "Fusion de plusieurs documents", "Nom ou rôle": "GT AM CORRES" 
- Ne modifie JAMAIS les balises doctags (<text>, <list_item>, etc.) ni les coordonnées <loc_X>.
- **Ne change pas l'ordre des balises, ne fusionne pas de balises, ne retire aucune balise, même vide.**
- **Si tu ne modifies pas une balise, recopie-la à l'identique.**

## Sortie attendue :
- Retourne UNIQUEMENT le contenu doctags corrigé et enrichi, sans explication, sans balise markdown, sans ```
- La sortie doit être strictement structurée comme l'entrée, avec toutes les balises présentes.

## Contenu doctags à enrichir :
{page_tags}

## URLs à insérer OBLIGATOIREMENT (dans l'ordre) :
{links_str if links_str else "Aucune URL pour cette page."}

IMPORTANT : Retourne UNIQUEMENT le contenu doctags corrigé et enrichi, sans explication, sans balise markdown, sans ``` 
"""
# Appel VLM

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
            return page_num, page_tags  # fallback : retourne le texte original

# Split / Rebuild doctags

def split_doctags_by_page(doctags: str, n_pages: int) -> dict[int, str]:
    """
    Divise le doctags en sections par page en utilisant les balises <page_break>.
    Si aucun <page_break> n'est trouvé, fallback sur la distribution par count.
    """
    # Split sur les balises <page_break> insérées par Docling
    parts = re.split(r'<page_break\s*/?>', doctags)

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
        end   = start + tags_per_page if i < n_pages - 1 else len(all_tags)
        pages[i + 1] = "\n".join(all_tags[start:end])
    return pages

def rebuild_doctags(header: str, pages: dict[int, str]) -> str:
    """Reconstruit le doctags complet depuis les pages traitées, sans lignes vides en excès."""
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
    """Extrait l'en-tête du doctags (avant la première balise)."""
    m = re.match(r'^(.*?)(?=<(?!doctag)\w+>)', doctags, re.DOTALL) # Ne pas changer car le VLM perd la structure après 
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

# Entrée

if __name__ == "__main__":
    BASE = project_root / "matthias_guideline_preprocessing"
    DATA = BASE / "data" / "output_files" / "stage3_test"

    # À adapter selon ton document
    DOC_NAME = os.environ.get("DOC_NAME", "")
    if not DOC_NAME:
        raise RuntimeError("DOC_NAME not set. Please define it in your .env.test.")

    pdf_path = BASE / "data" / "input_files" / f"{DOC_NAME}.pdf"
    doctags_path = BASE / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables.doctags"
    jsonl_path = DATA / DOC_NAME / f"hyperlinks_data_{DOC_NAME}.jsonl"
    output_path = DATA / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures_url_vlm.doctags"

    asyncio.run(run(pdf_path, doctags_path, jsonl_path, output_path))