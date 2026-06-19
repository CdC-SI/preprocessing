"""
docling_markdown_converter_modular.py — Conversion des doctags enrichis en Markdown.

Pré-traite le fichier .doctags (split pages, correction des balises mal placées),
le convertit en Markdown via Docling, puis post-traite les balises personnalisées
couleur et soulignement.

Usage :
    uv run python stage1_modulaire/docling_markdown_converter_modular.py \
        --input data/output_files/MonDoc/MonDoc.doctags
    uv run python stage1_modulaire/docling_markdown_converter_modular.py --dotenv .env.test
    uv run python stage1_modulaire/docling_markdown_converter_modular.py \
        --input  data/output_files/MonDoc/MonDoc.doctags \
        --output data/output_files/MonDoc/MonDoc.md
"""
import argparse
import logging
import os
import re
import sys
from pathlib import Path

from docling_core.types.doc.document import DocTagsDocument, DoclingDocument
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_log = logging.getLogger(__name__)


# Logique métier (fonctions pures)
def _split_pages(content: str) -> str:
    """
    Si le contenu est un seul bloc <doctag>, le découpe en un bloc par page en utilisant
    </page_footer> (doctags Docling natif) ou <page_break> (produit par url_tuning_vlm_modular.py)
    comme délimiteur, ce que from_multipage_doctags_and_images attend.
    Sans ce découpage, Docling s'arrête après la première page et ignore le reste.

    :param content: contenu doctags brut
    :type content: str
    :return: contenu découpé en blocs <doctag> par page
    :rtype: str
    """
    if content.count("<doctag>") > 1:
        return content  # déjà au bon format multi-pages

    inner = re.sub(r"^\s*</?doctag>\s*", "", content.strip(), flags=re.DOTALL)
    inner = re.sub(r"\s*</doctag>\s*$", "", inner, flags=re.DOTALL)

    # Tente d'abord </page_footer> (doctags Docling natif),
    # puis <page_break> (séparateur produit par url_tuning_vlm_modular.py et assemble_doctags).
    parts = re.split(r"(?<=</page_footer>)", inner)
    if len(parts) <= 1:
        parts = re.split(r"<page_break\s*/?>", inner)

    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) <= 1:
        return content  # document d'une seule page, rien à faire

    return "\n".join(f"<doctag>\n{p}\n</doctag>" for p in parts)


def _hoist_misplaced_tags(content: str) -> str:
    """
    Docling ne peut pas gérer les <section_header_level_N> ou <unordered_list> imbriqués
    dans un <ordered_list>. Il les écrase dans la liste, perdant les en-têtes et les frontières de section.
    Extrait ces balises des blocs <ordered_list> et les place juste après le </ordered_list> correspondant.

    :param content: contenu doctags
    :type content: str
    :return: contenu avec les balises mal placées extraites
    :rtype: str
    """
    HOIST = re.compile(
        r"(<section_header_level_\d[^>]*>.*?</section_header_level_\d>|"
        r"<unordered_list>.*?</unordered_list>)",
        re.DOTALL,
    )
    OL = re.compile(r"<ordered_list>(.*?)</ordered_list>", re.DOTALL)

    def _fix_ol(m: re.Match) -> str:
        inner = m.group(1)
        hoisted: list[str] = []

        def _extract(tag_m: re.Match) -> str:
            hoisted.append(tag_m.group(0))
            return ""

        cleaned = HOIST.sub(_extract, inner)
        result = f"<ordered_list>{cleaned}</ordered_list>"
        if hoisted:
            result += "\n" + "\n".join(hoisted)
        return result

    return OL.sub(_fix_ol, content)


def preprocess_doctags(content: str) -> str:
    """
    Pré-traite le contenu doctags : découpage en pages et correction des balises mal placées.
    Point d'entrée public pour les modules externes — évite de coupler sur les helpers privés.

    :param content: contenu doctags brut
    :return: contenu pré-traité, prêt pour DocTagsDocument
    """
    content = _split_pages(content)
    content = _hoist_misplaced_tags(content)
    return content


def _replace_color(match: re.Match) -> str:
    """
    Remplace les balises personnalisées [[COLOR:color]]texte[[/COLOR]]
    par des spans HTML <span style="color:color">texte</span>.

    :param match: résultat de re.sub avec groupes (color, texte)
    :return: span HTML avec la couleur appliquée
    :rtype: str
    """
    return f'<span style="color:{match.group(1)}">{match.group(2)}</span>'


def _replace_underline(match: re.Match) -> str:
    """
    Remplace les balises de soulignement personnalisées \\_\\_texte\\_\\_
    par des balises HTML <u>texte</u>.

    :param match: résultat de re.sub avec groupe (texte)
    :return: balise HTML <u> avec le texte souligné
    :rtype: str
    """
    return f'<u>{match.group(1)}</u>'


def convert_doctags_to_markdown(doctags_path: Path) -> str:
    """
    Docstring for convert_doctags_to_markdown
    Lit le fichier .doctags, applique les pré-traitements, convertit en Markdown via Docling,
    puis applique les post-traitements des balises personnalisées couleur et soulignement.

    :param doctags_path: chemin vers le fichier .doctags à convertir
    :type doctags_path: Path
    :return: contenu Markdown final
    :rtype: str
    """
    content = doctags_path.read_text(encoding="utf-8")
    content = _split_pages(content)
    content = _hoist_misplaced_tags(content)

    doctags_doc = DocTagsDocument.from_multipage_doctags_and_images(content, None)
    doc = DoclingDocument.load_from_doctags(doctags_doc)
    markdown = doc.export_to_markdown()

    _root = str(Path(__file__).resolve().parent.parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from utils.markdown_utils import apply_markdown_transforms
    return apply_markdown_transforms(markdown)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convertit un fichier .doctags enrichi en Markdown via Docling. "
            "Pré-traite les pages et les balises mal placées, "
            "post-traite les balises couleur et soulignement personnalisées."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python stage1_modulaire/docling_markdown_converter_modular.py \\\n"
            "      --input data/output_files/MonDoc/MonDoc.doctags\n\n"
            "  # Sortie personnalisée :\n"
            "  uv run python stage1_modulaire/docling_markdown_converter_modular.py \\\n"
            "      --input  data/output_files/MonDoc/MonDoc.doctags \\\n"
            "      --output data/output_files/MonDoc/MonDoc.md\n\n"
            "  # Via variable d'environnement DOC_NAME :\n"
            "  uv run python stage1_modulaire/docling_markdown_converter_modular.py \\\n"
            "      --dotenv .env.test\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help=(
            "Fichier .doctags à convertir. "
            "Si absent, résout data/output_files/<DOC_NAME>/<DOC_NAME>.doctags depuis l'environnement."
        ),
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Fichier Markdown de sortie. "
            "Défaut : data/output_files/<stem>/<stem>.md"
        ),
    )
    parser.add_argument(
        "--suffix", "-s",
        type=str,
        default="",
        metavar="SUFFIXE",
        help=(
            "Suffixe à ajouter au nom du fichier .doctags résolu automatiquement. "
            "Ex. : --suffix _url_vlm → <DOC_NAME>_url_vlm.doctags. "
            "Ignoré si --input est fourni."
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
def resolve_input(args: argparse.Namespace) -> Path:
    """
    Docstring for resolve_input
    Résout le chemin du fichier .doctags à convertir :
    1. --input fourni → utilisé directement.
    2. Sinon → lit DOC_NAME depuis l'environnement (le dotenv est déjà chargé dans main()).

    :param args: Description
    :type args: argparse.Namespace
    :return: Description
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if not doc_name:
        raise SystemExit(
            "Erreur : fournir --input <chemin>, ou --dotenv <fichier> avec DOC_NAME, "
            "ou définir la variable DOC_NAME dans l'environnement."
        )
    suffix = getattr(args, "suffix", "")
    return _PROJECT_ROOT / "data" / "output_files" / doc_name / f"{doc_name}{suffix}.doctags"


def resolve_output(args: argparse.Namespace, input_path: Path) -> Path:
    """
    Docstring for resolve_output
    Résout le chemin du fichier Markdown de sortie :
    1. --output fourni → utilisé directement.
    2. Sinon → data/output_files/<stem>/<stem>.md

    :param args: Description
    :type args: argparse.Namespace
    :param input_path: Description
    :type input_path: Path
    :return: Description
    :rtype: Path
    """
    if args.output:
        return args.output.resolve()
    return input_path.parent / f"{input_path.stem}.md"


# Point d'entrée
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    if args.dotenv:
        dotenv_path = args.dotenv.resolve()
        if not dotenv_path.exists():
            raise SystemExit(f"Erreur : fichier .env introuvable — {dotenv_path}")
        load_dotenv(dotenv_path=dotenv_path)
        _log.info("Environnement chargé depuis : %s", dotenv_path)

    input_path = resolve_input(args)
    if not input_path.exists():
        raise SystemExit(f"Erreur : fichier .doctags introuvable — {input_path}")

    output_path = resolve_output(args, input_path)

    _log.info("Entrée  : %s", input_path)
    _log.info("Sortie  : %s", output_path)

    try:
        markdown = convert_doctags_to_markdown(input_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    except Exception:
        _log.exception("Erreur lors de la conversion de %s", input_path.name)
        sys.exit(1)

    _log.info("Markdown généré : %s", output_path)
    sys.exit(0)


if __name__ == "__main__":
    main()
