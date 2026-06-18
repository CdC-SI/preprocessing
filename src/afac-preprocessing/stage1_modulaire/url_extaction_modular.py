"""
url_extaction_modular.py — Extraction des liens hypertextes (URL, mailto) depuis un PDF.

Utilise PyMuPDF pour extraire les liens externes de chaque page et associe
le texte des mots dont le centre se trouve dans le rectangle du lien.
Produit un fichier JSONL — une ligne par lien trouvé.

Se lance indépendamment ou après pipeline_multietape_modulaire.py.

Usage :
    uv run python url_extaction_modular.py --pdf data/input_files/MonDoc.pdf
    uv run python url_extaction_modular.py --dotenv .env.test
"""
import argparse
import logging
import os
import sys
from pathlib import Path
import fitz  # PyMuPDF
import jsonlines
from dotenv import load_dotenv

_log = logging.getLogger(__name__)


# Logique métier (fonctions pures)
def is_external_link(uri: str | None) -> bool:
    """
    Docstring for is_external_link
    Retourne True si l'URI est un lien externe (http, https, mailto).

    :param uri: Description
    :type uri: str | None
    :return: Description
    :rtype: bool
    """
    return bool(uri and uri.startswith(("http://", "https://", "mailto:")))


def get_link_text(link: dict, words: list[tuple]) -> str:
    """
    Docstring for get_link_text
    Retourne le texte des mots dont le centre se trouve dans le rectangle du lien.

    :param link: Description
    :type link: dict
    :param words: Description
    :type words: list[tuple]
    :return: Description
    :rtype: str
    """
    rect = link.get("from")
    if not rect:
        return "No text"
    rx0, ry0, rx1, ry1 = rect
    link_words = [
        w[4] for w in words
        if rx0 <= (w[0] + w[2]) / 2 <= rx1 and ry0 <= (w[1] + w[3]) / 2 <= ry1
    ]
    return " ".join(link_words).strip() if link_words else "No text"


def serialize_link(link: dict) -> dict:
    """
    Docstring for serialize_link
    Convertit fitz.Rect en liste pour la sérialisation JSON.

    :param link: Description
    :type link: dict
    :return: Description
    :rtype: dict
    """
    link_serializable = link.copy()
    if "from" in link_serializable and isinstance(link_serializable["from"], fitz.Rect):
        link_serializable["from"] = list(link_serializable["from"])
    return link_serializable


def extract_url_links(pdf_path: Path) -> list[dict]:
    """
    Docstring for extract_url_links
    Extrait tous les liens externes du PDF page par page.
    Retourne une liste de dicts avec page_number, text, hyperlink, type, details.

    :param pdf_path: Description
    :type pdf_path: Path
    :return: Description
    :rtype: list[dict]
    """
    results = []
    with fitz.open(pdf_path) as doc:
        for page_num in range(len(doc)):
            page = doc[page_num]
            links = page.get_links()
            words = page.get_text("words")
            page_links = [
                {
                    "page_number": page_num + 1,
                    "text": get_link_text(link, words),
                    "hyperlink": link.get("uri"),
                    "type": "URI",
                    "details": serialize_link(link),
                }
                for link in links
                if is_external_link(link.get("uri"))
            ]
            if page_links:
                _log.info("  Page %d : %d lien(s)", page_num + 1, len(page_links))
            results.extend(page_links)
    return results


def save_links(links: list[dict], output_path: Path) -> None:
    """
    Docstring for save_links
    Écrit la liste de liens dans un fichier JSONL.

    :param links: Description
    :type links: list[dict]
    :param output_path: Description
    :type output_path: Path
    """
    with jsonlines.open(output_path, mode="w") as writer:
        for item in links:
            writer.write(item)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extrait les liens hypertextes (http, https, mailto) d'un PDF "
            "et les sauvegarde dans un fichier JSONL."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python url_extaction_modular.py --pdf data/input_files/MonDoc.pdf\n"
            "  uv run python url_extaction_modular.py "
            "--pdf data/input_files/MonDoc.pdf "
            "--output data/output_files/MonDoc/hyperlinks_data_MonDoc.jsonl\n"
            "  uv run python url_extaction_modular.py --dotenv .env.test\n"
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
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Fichier JSONL de sortie. "
            "Défaut : data/output_files/<nom_pdf>/hyperlinks_data_<nom_pdf>.jsonl"
        ),
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
def _project_root() -> Path:
    """
    Docstring for _project_root
    Va jusqu'au répertoire racine du projet (deux niveaux au-dessus de ce fichier).
    :return: Description
    :rtype: Path
    """
    return Path(__file__).resolve().parent.parent


def resolve_pdf(args: argparse.Namespace) -> Path:
    """
    Docstring for resolve_pdf
    Résout le chemin du PDF à traiter selon la logique suivante :
    1. Si --pdf est fourni, utilise ce chemin.
    2. Sinon, si --dotenv est fourni, charge ce fichier .env et lit DOC_NAME pour construire le chemin data/input_files/<DOC_NAME>.pdf.
    3. Sinon, lit DOC_NAME depuis l'environnement et construit le chemin data/input_files/<DOC_NAME>.pdf.
    4. Si DOC_NAME n'est pas défini ou vide, affiche une erreur et quitte.

    :param args: Description
    :type args: argparse.Namespace
    :return: Description
    :rtype: Path
    """
    if args.pdf:
        return args.pdf.resolve()
    if args.dotenv:
        dotenv_path = args.dotenv.resolve()
        if not dotenv_path.exists():
            raise SystemExit(f"Erreur : fichier .env introuvable — {dotenv_path}")
        load_dotenv(dotenv_path=dotenv_path)
        _log.info("Environnement chargé depuis : %s", dotenv_path)
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if not doc_name:
        raise SystemExit(
            "Erreur : fournir --pdf <chemin>, ou --dotenv <fichier> avec DOC_NAME, "
            "ou définir la variable DOC_NAME dans l'environnement."
        )
    return _project_root() / "data" / "input_files" / f"{doc_name}.pdf"


def resolve_output(args: argparse.Namespace, pdf_path: Path) -> Path:
    """
    Docstring for resolve_output
    Résout le chemin du fichier de sortie JSONL selon la logique suivante :
    1. Si --output est fourni, utilise ce chemin.
    2. Sinon, construit le chemin par défaut : data/output_files/<nom_pdf>/hyperlinks_data_<nom_pdf>.jsonl

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
    return _project_root() / "data" / "output_files" / stem / f"hyperlinks_data_{stem}.jsonl"


# Point d'entrée
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    pdf_path = resolve_pdf(args)
    if not pdf_path.exists():
        raise SystemExit(f"Erreur : fichier PDF introuvable — {pdf_path}")

    output_path = resolve_output(args, pdf_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _log.info("PDF source : %s", pdf_path)
    _log.info("Sortie     : %s", output_path)

    try:
        links = extract_url_links(pdf_path)
        save_links(links, output_path)
    except Exception:
        _log.exception("Erreur lors de l'extraction des liens de %s", pdf_path.name)
        sys.exit(1)

    if links:
        _log.info("Terminé — %d lien(s) extrait(s) → %s", len(links), output_path)
    else:
        _log.info("Terminé — aucun lien externe trouvé dans %s", pdf_path.name)

    sys.exit(0)


if __name__ == "__main__":
    main()
