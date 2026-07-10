"""
url_tuning_vlm.py — Intégration des liens hypertextes dans le doctags via VLM.

Utilise le VLM pour reconstruire le doctags page par page en y intégrant
les liens extraits (URL, mailto) au format markdown [text](url).

Fonctionne en standalone ou en bout de pipeline stage3.

Usage :
    uv run python url_tuning_vlm.py --input doc.pdf --doctags doc.doctags --jsonl links.jsonl
    uv run python url_tuning_vlm.py --dotenv .env.test
    uv run python url_tuning_vlm.py --input data/input_files/MonDoc.pdf
"""
import argparse
import asyncio
import base64
import json
import logging
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
from openai import AsyncOpenAI

from prompts.prompts import VLM_PROMPT_CORRECTION_STAGE_3_EN, VLM_PROMPT_CORRECTION_STAGE_3_EN_V3
from utils.paths import project_root, load_env, resolve_doc_name, resolve_input_pdf
from utils.vlm_client import (
    build_async_client,
    build_vlm_config,
    check_vlm_connectivity_async,
    vision_completion_async,
)

_log = logging.getLogger(__name__)

PROMPT_VARIANTS = {
    "v2": VLM_PROMPT_CORRECTION_STAGE_3_EN,     # tables JSON lines (post load_jsonline_doctags)
    "v3": VLM_PROMPT_CORRECTION_STAGE_3_EN_V3,  # tables <otsl> natives, préservées telles quelles
}


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


def build_prompt(page_tags: str, page_links: list[dict], prompt_template: str) -> str:
    """
    Docstring for build_prompt
    - Construit le prompt à envoyer au VLM en intégrant les doctags de la page et les liens extraits du JSONL.
    - Les liens sont formatés de manière lisible pour le VLM, avec leur texte associé et leur URL,
    pour lui permettre de les intégrer correctement dans le contenu doctags qu'il va reconstruire pour la page.

    :param page_tags: Description
    :type page_tags: str
    :param page_links: Description
    :type page_links: list[dict]
    :param prompt_template: Template de prompt à formater (v2 ou v3, cf. --prompt-variant)
    :type prompt_template: str
    :return: Description
    :rtype: str
    """
    links_str = "\n".join(
        f'{i+1}. texte: "{l["text"]}" -> url: {l["hyperlink"]}'
        for i, l in enumerate(page_links)
    )
    if not links_str:
        links_str = "Aucune URL pour cette page."

    return prompt_template.format(
        page_tags=page_tags,
        links_str=links_str,
    )


async def process_page(
    page_num: int,
    page_tags: str,
    page_links: list[dict],
    pdf_path: Path,
    semaphore: asyncio.Semaphore,
    *,
    client: AsyncOpenAI,
    model_name: str,
    prompt_template: str,
) -> tuple[int, str]:
    """
    Traite une page du PDF en appelant le VLM pour reconstruire son contenu doctags enrichi
    avec les liens URL. Le retry sur erreur transitoire est géré par le client OpenAI
    (max_retries, cf. utils.vlm_client.build_async_client) — plus de boucle manuelle ici.

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
    :param prompt_template: Template de prompt à formater (v2 ou v3, cf. --prompt-variant)
    :type prompt_template: str
    :return: Description
    :rtype: tuple[int, str]
    """
    async with semaphore:
        _log.info("Page %d : %d lien(s) à insérer...", page_num, len(page_links))
        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num)
            prompt = build_prompt(page_tags, page_links, prompt_template)
            result = await vision_completion_async(client, model_name, prompt, image_b64)
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

    Ré-indexe séquentiellement sur les segments non vides plutôt que sur l'index brut du split :
    un <page_break> superflu ou consécutif (ex. artefact d'un correctif amont) produit un
    segment vide qui, indexé par position brute, décale tous les numéros de page suivants —
    la page N se retrouve alors stockée sous la clé N+1, et run() ne la trouve jamais
    (pages_tags.get(N, "") silencieusement vide → contenu de page perdu sans erreur).

    :param doctags: Description
    :type doctags: str
    :param n_pages: Description
    :type n_pages: int
    :return: Description
    :rtype: dict[int, str]
    """
    parts = re.split(r'<page_break\s*/?>', doctags)
    non_empty = [content for p in parts if (content := p.strip())]

    if len(non_empty) > 1:
        _log.info("Split par <page_break> : %d page(s) détectées.", len(non_empty))
        if len(non_empty) != n_pages:
            _log.warning(
                "%d page(s) détectées via <page_break> mais %d page(s) attendues (PDF) — "
                "vérifier le doctags source pour des <page_break> en trop ou manquants.",
                len(non_empty), n_pages,
            )
        return {i + 1: content for i, content in enumerate(non_empty)}

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
        s = re.sub(r'</?doctags?>', '', s, flags=re.IGNORECASE)
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
    *,
    client: AsyncOpenAI,
    model_name: str,
    prompt_template: str = VLM_PROMPT_CORRECTION_STAGE_3_EN,
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
    if not await check_vlm_connectivity_async(client, model_name):
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
            client=client,
            model_name=model_name,
            prompt_template=prompt_template,
        )
        for p in range(1, n_pages + 1)
        if pages_tags.get(p, "").strip()
    ]

    try:
        results = await asyncio.gather(*tasks)
    finally:
        await client.close()

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
            "  uv run python url_tuning_vlm.py \\\n"
            "      --input data/input_files/MonDoc.pdf \\\n"
            "      --doctags data/output_files_preprocessing/MonDoc/MonDoc.doctags \\\n"
            "      --jsonl data/output_files_preprocessing/MonDoc/hyperlinks_data_MonDoc.jsonl\n\n"
            "  # Chemins résolus automatiquement depuis le stem du PDF :\n"
            "  uv run python url_tuning_vlm.py --input data/input_files/MonDoc.pdf\n\n"
            "  # Via variable d'environnement DOC_NAME :\n"
            "  uv run python url_tuning_vlm.py --dotenv .env.test\n"
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
        "--doctags", "-d",
        type=Path,
        default=None,
        help=(
            "Fichier doctags d'entrée à enrichir. "
            "Défaut : data/output_files_preprocessing/<stem>/<stem>_reordered_with_tables_pictures.doctags"
        ),
    )
    parser.add_argument(
        "--jsonl", "-j",
        type=Path,
        default=None,
        help=(
            "Fichier JSONL contenant les liens hypertextes extraits. "
            "Défaut : data/output_files_preprocessing/<stem>/hyperlinks_data_<stem>.jsonl"
        ),
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Chemin du fichier doctags de sortie. "
            "Défaut : data/output_files_preprocessing/<stem>/<stem>_url_vlm.doctags"
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
        "--prompt-variant",
        choices=sorted(PROMPT_VARIANTS),
        default="v2",
        help=(
            "v2 : tables JSON lines (pipeline avec load_jsonline_doctags.py). "
            "v3 : tables <otsl> Docling natives, préservées telles quelles (pipeline sans conversion JSON). "
            "Défaut : v2."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger pour résoudre DOC_NAME (ex. : .env.test). Ignoré si --input est fourni.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de journalisation. Défaut : INFO.",
    )
    return parser.parse_args()


# Résolution des chemins
def resolve_pdf(args: argparse.Namespace) -> Path:
    """
    Docstring for resolve_pdf
    Résout le chemin du PDF selon la logique suivante :
    1. --input fourni → utilisé directement.
    2. Sinon → lit DOC_NAME depuis l'environnement (le dotenv est déjà chargé dans main()).

    :param args: Description
    :type args: argparse.Namespace
    :return: Description
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    doc_name = resolve_doc_name(args, primary_flag="--input")
    return resolve_input_pdf(doc_name)


def resolve_doctags(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Docstring for resolve_doctags
    Résout le chemin du fichier doctags d'entrée.
    Si --doctags est fourni, l'utilise directement.
    Sinon, construit le chemin par défaut : data/output_files_preprocessing/<stem>/<stem>.doctags

    :param args: Description
    :type args: argparse.Namespace
    :param pdf_path: Description
    :type pdf_path: Path
    :return: Description
    :rtype: Path
    """
    if args.doctags:
        return args.doctags.resolve()
    stem = pdf_path.stem.strip()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_reordered_with_tables_pictures.doctags"


def resolve_jsonl(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Docstring for resolve_jsonl
    Résout le chemin du fichier JSONL contenant les liens.
    Si --jsonl est fourni, l'utilise directement.
    Sinon, construit le chemin par défaut : data/output_files_preprocessing/<stem>/hyperlinks_data_<stem>.jsonl

    :param args: Description
    :type args: argparse.Namespace
    :param pdf_path: Description
    :type pdf_path: Path
    :return: Description
    :rtype: Path
    """
    if args.jsonl:
        return args.jsonl.resolve()
    stem = pdf_path.stem.strip()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"hyperlinks_data_{stem}.jsonl"


def resolve_output(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Docstring for resolve_output
    Résout le chemin du fichier doctags de sortie.
    Si --output est fourni, l'utilise directement.
    Sinon, construit le chemin par défaut : data/output_files_preprocessing/<stem>/<stem>_url_vlm.doctags

    :param args: Description
    :type args: argparse.Namespace
    :param pdf_path: Description
    :type pdf_path: Path
    :return: Description
    :rtype: Path
    """
    if args.output:
        return args.output.resolve()
    stem = pdf_path.stem.strip()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_url_vlm.doctags"


# Point d'entrée
def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    vlm_cfg = build_vlm_config(dotenv_path=args.dotenv)
    client = build_async_client(vlm_cfg)

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
            client=client,
            model_name=vlm_cfg.vlm_model_name,
            prompt_template=PROMPT_VARIANTS[args.prompt_variant],
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
