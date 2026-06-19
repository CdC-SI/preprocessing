"""
url_tuning_vlm_modular.py — Intégration des liens hypertextes dans le doctags via VLM.

Utilise le VLM pour reconstruire le doctags page par page en y intégrant
les liens extraits (URL, mailto) au format markdown [text](url).

Fonctionne en standalone ou en bout de pipeline stage3.

Usage :
    uv run python url_tuning_vlm_modular.py --pdf doc.pdf --doctags doc.doctags --jsonl links.jsonl
    uv run python url_tuning_vlm_modular.py --dotenv .env.test
    uv run python url_tuning_vlm_modular.py --pdf data/input_files/MonDoc.pdf
"""
import argparse
import asyncio
import base64
import json
import logging
import os
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import httpx
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from prompts.prompts import VLM_PROMPT_CORRECTION_STAGE_3_EN
from utils.config import load_vlm_config

_log = logging.getLogger(__name__)

# Initialisé dans main() pour éviter les effets de bord à l'import
CA_PATH: str = ""
VLM_URL: str = ""
VLM_MODEL_NAME: str = ""


def _load_vlm_config() -> None:
    global CA_PATH, VLM_URL, VLM_MODEL_NAME
    cfg = load_vlm_config()
    CA_PATH = cfg["CA_PATH"]
    VLM_URL = cfg["VLM_URL"]
    VLM_MODEL_NAME = cfg["VLM_MODEL_NAME"]


# Logique VLM
async def call_vlm_async(prompt: str, image_b64: str) -> str:
    """
    Docstring for call_vlm_async
    - Appelle le VLM de manière asynchrone en lui envoyant un prompt et une image encodée en base64,
    et retourne la réponse textuelle du VLM.

    :param prompt: Description
    :type prompt: str
    :param image_b64: Description
    :type image_b64: str
    :return: Description
    :rtype: str
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
        "chat_template_kwargs": {"enable_thinking": False}, # désactive le mode thinking Qwen3.5 (évite content=null)
    }
    async with httpx.AsyncClient(verify=CA_PATH, timeout=120) as client:
        resp = await client.post(VLM_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        content = message.get("content")
        if content is not None:
            return content.strip()
        _log.warning("content=null, full message: %s", message)
        raise ValueError(f"VLM returned null content. Full message: {message}")


async def check_vlm_connectivity() -> bool:
    """
    Docstring for check_vlm_connectivity
    - Effectue un test de connectivité au VLM en lui envoyant un prompt simple ("ping") et en vérifiant la réponse.
    - Utile pour s'assurer que le VLM est accessible avant de lancer le pipeline de traitement des pages,
    et éviter de lancer un traitement long qui échouerait ensuite faute de connectivité.

    :return: Description
    :rtype: bool
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


# Logique métier (fonctions pures)
def load_jsonl_links(jsonl_path: Path) -> list[dict]:
    """
    Docstring for load_jsonl_links
    - Charge les liens extraits du fichier JSONL en une liste de dictionnaires.

    :param jsonl_path: Description
    :type jsonl_path: Path
    :return: Description
    :rtype: list[dict]
    """
    links = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                links.append(json.loads(line))
    return links


def get_links_for_page(links: list[dict], page: int) -> list[dict]:
    """
    Docstring for get_links_for_page
    - Filtre la liste de liens pour ne retourner que ceux qui correspondent à la page spécifiée.

    :param links: Description
    :type links: list[dict]
    :param page: Description
    :type page: int
    :return: Description
    :rtype: list[dict]
    """
    return [l for l in links if l.get("page_number") == page]


def pdf_page_to_base64(pdf_path: Path, page_num: int) -> str:
    """
    Docstring for pdf_page_to_base64
    - Ouvre le PDF, rend la page spécifiée en image, et encode cette image en base64 pour l'envoyer au VLM.

    :param pdf_path: Description
    :type pdf_path: Path
    :param page_num: Description
    :type page_num: int
    :return: Description
    :rtype: str
    """
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[page_num - 1].get_pixmap(dpi=100)
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")


def build_prompt(page_tags: str, page_links: list[dict]) -> str:
    """
    Docstring for build_prompt
    - Construit le prompt à envoyer au VLM en intégrant les doctags de la page et les liens extraits du JSONL.
    - Les liens sont formatés de manière lisible pour le VLM, avec leur texte associé et leur URL,
    pour lui permettre de les intégrer correctement dans le contenu doctags qu'il va reconstruire pour la page.

    :param page_tags: Description
    :type page_tags: str
    :param page_links: Description
    :type page_links: list[dict]
    :return: Description
    :rtype: str
    """
    links_str = "\n".join(
        f'{i+1}. texte: "{l["text"]}" -> url: {l["hyperlink"]}'
        for i, l in enumerate(page_links)
    )
    if not links_str:
        links_str = "Aucune URL pour cette page."

    return VLM_PROMPT_CORRECTION_STAGE_3_EN.format(
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
    """
    Docstring for process_page
    - Traite une page du PDF en appelant le VLM pour reconstruire son contenu doctags enrichi avec les liens URL.

    :param page_num: Description
    :type page_num: int
    :param page_tags: Description
    :type page_tags: str
    :param page_links: Description
    :type page_links: list[dict]
    :param pdf_path: Description
    :type pdf_path: Path
    :param semaphore: Description
    :type semaphore: asyncio.Semaphore
    :return: Description
    :rtype: tuple[int, str]
    """
    async with semaphore:
        _log.info("Page %d : %d lien(s) à insérer...", page_num, len(page_links))
        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num)
            prompt = build_prompt(page_tags, page_links)
            result = await call_vlm_async(prompt, image_b64)
            _log.info("Page %d traitée.", page_num)
            return page_num, result
        except Exception as e:
            _log.exception("Erreur page %d : %s", page_num, e)
            return page_num, page_tags  # fallback : retourne le doctags original de la page


def split_doctags_by_page(doctags: str, n_pages: int) -> dict[int, str]:
    """
    Docstring for split_doctags_by_page
    - Tente de diviser le contenu doctags en pages en utilisant les balises <page_break> comme séparateurs.
    - Si les balises <page_break> sont présentes, on split directement le contenu en fonction de ces balises,
    ce qui est plus fiable pour associer les bonnes parties du doctags à chaque page.
    - Si aucune balise de séparation n'est trouvée, utilise une approche de fallback qui distribue les éléments du doctags
    de manière approximative en fonction du nombre total.

    :param doctags: Description
    :type doctags: str
    :param n_pages: Description
    :type n_pages: int
    :return: Description
    :rtype: dict[int, str]
    """
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
    """
    Docstring for assemble_doctags
    - Assemble les contenus doctags de chaque page en un seul contenu global, en s'assurant de retirer les balises doctag internes
    pour éviter les problèmes d'imbrication, et en collant proprement les contenus avec des sauts de ligne.

    :param pages: Description
    :type pages: dict[int, str]
    :return: Description
    :rtype: str
    """
    def _strip_doctag_tags(s: str) -> str:
        s = re.sub(r'</?doctag[s]?>', '', s, flags=re.IGNORECASE)
        return s.strip()

    parts = []
    for page_num in sorted(pages):
        content = pages[page_num].strip()
        if not content:
            continue
        content = _strip_doctag_tags(content)
        content = re.sub(r'\n{2,}', '\n', content)
        if content:
            parts.append(content)
    body = "\n<page_break>\n".join(parts).strip()
    return f"<doctag>\n{body}\n</doctag>"


# Pipeline principal
async def run(
    pdf_path: Path,
    doctags_path: Path,
    jsonl_path: Path,
    output_path: Path,
    max_workers: int = 1,
) -> None:
    """
    Docstring for run
    - Point d'entrée principal du script : vérifie la connectivité au VLM, charge les données nécessaires, traite chaque page du PDF en parallèle,
    et assemble les résultats en un fichier doctags final.
    - Affiche des informations sur le nombre de pages et de liens détectés, pour donner une idée de l'ampleur du travail à réaliser.
    - Utilise un sémaphore pour limiter le nombre de requêtes simultanées au VLM,
    afin de ne pas le saturer et de gérer les ressources de manière efficace.

    :param pdf_path: Description
    :type pdf_path: Path
    :param doctags_path: Description
    :type doctags_path: Path
    :param jsonl_path: Description
    :type jsonl_path: Path
    :param output_path: Description
    :type output_path: Path
    :param max_workers: Description
    :type max_workers: int
    """
    if not await check_vlm_connectivity():
        raise RuntimeError("VLM inaccessible, arrêt du pipeline.")

    _log.info("=" * 60)
    _log.info("PDF      : %s", pdf_path)
    _log.info("Doctags  : %s", doctags_path)
    _log.info("JSONL    : %s", jsonl_path)
    _log.info("Sortie   : %s", output_path)
    _log.info("Workers  : %d", max_workers)
    _log.info("=" * 60)

    doctags = doctags_path.read_text(encoding="utf-8")
    links = load_jsonl_links(jsonl_path)

    with fitz.open(str(pdf_path)) as doc:
        n_pages = doc.page_count
    _log.info("%d page(s) détectées, %d lien(s) au total.", n_pages, len(links))

    pages_tags = split_doctags_by_page(doctags, n_pages)

    semaphore = asyncio.Semaphore(max_workers)
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

    processed_pages = dict(sorted(results))
    final_doctags = assemble_doctags(processed_pages)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_doctags, encoding="utf-8")
    _log.info("Doctags final sauvegardé : %s", output_path)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Intègre les liens hypertextes dans le doctags via VLM, page par page. "
            "Produit un fichier doctags enrichi avec les URLs au format markdown [text](url)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python url_tuning_vlm_modular.py \\\n"
            "      --pdf data/input_files/MonDoc.pdf \\\n"
            "      --doctags data/output_files/MonDoc/MonDoc.doctags \\\n"
            "      --jsonl data/output_files/MonDoc/hyperlinks_data_MonDoc.jsonl\n\n"
            "  # Chemins résolus automatiquement depuis le stem du PDF :\n"
            "  uv run python url_tuning_vlm_modular.py --pdf data/input_files/MonDoc.pdf\n\n"
            "  # Via variable d'environnement DOC_NAME :\n"
            "  uv run python url_tuning_vlm_modular.py --dotenv .env.test\n"
        ),
    )
    parser.add_argument(
        "--pdf", "-p",
        type=Path,
        default=None,
        help=(
            "Chemin vers le PDF source. "
            "Si absent, résout data/input_files/<DOC_NAME>.pdf depuis l'environnement."
        ),
    )
    parser.add_argument(
        "--doctags", "-d",
        type=Path,
        default=None,
        help=(
            "Fichier doctags d'entrée à enrichir. "
            "Défaut : data/output_files/<stem>/<stem>.doctags"
        ),
    )
    parser.add_argument(
        "--jsonl", "-j",
        type=Path,
        default=None,
        help=(
            "Fichier JSONL contenant les liens hypertextes extraits. "
            "Défaut : data/output_files/<stem>/hyperlinks_data_<stem>.jsonl"
        ),
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Chemin du fichier doctags de sortie. "
            "Défaut : data/output_files/<stem>/<stem>_url_vlm.doctags"
        ),
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=1,
        metavar="N",
        help="Nombre de requêtes VLM simultanées. Défaut : 1.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger pour résoudre DOC_NAME (ex. : .env.test). Ignoré si --pdf est fourni.",
    )
    return parser.parse_args()


# Résolution des chemins
def resolve_pdf(args: argparse.Namespace) -> Path:
    """
    Docstring for resolve_pdf
    Résout le chemin du PDF selon la logique suivante :
    1. --pdf fourni → utilisé directement.
    2. Sinon → lit DOC_NAME depuis l'environnement (le dotenv est déjà chargé dans main()).

    :param args: Description
    :type args: argparse.Namespace
    :return: Description
    :rtype: Path
    """
    if args.pdf:
        return args.pdf.resolve()
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if not doc_name:
        raise SystemExit(
            "Erreur : fournir --pdf <chemin>, ou --dotenv <fichier> avec DOC_NAME, "
            "ou définir la variable DOC_NAME dans l'environnement."
        )
    return _PROJECT_ROOT / "data" / "input_files" / f"{doc_name}.pdf"


def resolve_doctags(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Docstring for resolve_doctags
    Résout le chemin du fichier doctags d'entrée.
    Si --doctags est fourni, l'utilise directement.
    Sinon, construit le chemin par défaut : data/output_files/<stem>/<stem>.doctags

    :param args: Description
    :type args: argparse.Namespace
    :param pdf_path: Description
    :type pdf_path: Path
    :return: Description
    :rtype: Path
    """
    if args.doctags:
        return args.doctags.resolve()
    stem = pdf_path.stem
    return _PROJECT_ROOT / "data" / "output_files" / stem / f"{stem}.doctags"


def resolve_jsonl(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Docstring for resolve_jsonl
    Résout le chemin du fichier JSONL contenant les liens.
    Si --jsonl est fourni, l'utilise directement.
    Sinon, construit le chemin par défaut : data/output_files/<stem>/hyperlinks_data_<stem>.jsonl

    :param args: Description
    :type args: argparse.Namespace
    :param pdf_path: Description
    :type pdf_path: Path
    :return: Description
    :rtype: Path
    """
    if args.jsonl:
        return args.jsonl.resolve()
    stem = pdf_path.stem
    return _PROJECT_ROOT / "data" / "output_files" / stem / f"hyperlinks_data_{stem}.jsonl"


def resolve_output(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Docstring for resolve_output
    Résout le chemin du fichier doctags de sortie.
    Si --output est fourni, l'utilise directement.
    Sinon, construit le chemin par défaut : data/output_files/<stem>/<stem>_url_vlm.doctags

    :param args: Description
    :type args: argparse.Namespace
    :param pdf_path: Description
    :type pdf_path: Path
    :return: Description
    :rtype: Path
    """
    if args.output:
        return args.output.resolve()
    stem = pdf_path.stem
    return _PROJECT_ROOT / "data" / "output_files" / stem / f"{stem}_url_vlm.doctags"


# Point d'entrée
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    # Charger le dotenv avant _load_vlm_config() pour que ses variables soient disponibles
    if args.dotenv:
        dotenv_path = args.dotenv.resolve()
        if not dotenv_path.exists():
            raise SystemExit(f"Erreur : fichier .env introuvable — {dotenv_path}")
        load_dotenv(dotenv_path=dotenv_path)
        _log.info("Environnement chargé depuis : %s", dotenv_path)

    _load_vlm_config()


    pdf_path = resolve_pdf(args)
    if not pdf_path.exists():
        raise SystemExit(f"Erreur : fichier PDF introuvable — {pdf_path}")

    doctags_path = resolve_doctags(args, pdf_path)
    if not doctags_path.exists():
        raise SystemExit(
            f"Erreur : fichier doctags introuvable — {doctags_path}\n"
            "Conseil : utilisez --doctags <chemin> pour spécifier le fichier d'entrée."
        )

    jsonl_path = resolve_jsonl(args, pdf_path)
    if not jsonl_path.exists():
        raise SystemExit(
            f"Erreur : fichier JSONL introuvable — {jsonl_path}\n"
            "Conseil : utilisez --jsonl <chemin> pour spécifier le fichier de liens."
        )

    output_path = resolve_output(args, pdf_path)

    try:
        asyncio.run(run(
            pdf_path=pdf_path,
            doctags_path=doctags_path,
            jsonl_path=jsonl_path,
            output_path=output_path,
            max_workers=args.workers,
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
