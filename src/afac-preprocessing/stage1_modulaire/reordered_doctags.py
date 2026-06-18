"""
reordered_doctags.py — Réordonnancement des blocs d'un fichier .doctags par coordonnées y0/x0.

Docling peut extraire les blocs dans un ordre incorrect quand des coordonnées y0 sont
similaires ou absentes. Ce script les retrie par position verticale (y0) puis horizontale (x0),
page par page, avant les étapes VLM aval.

Se lance après pipeline_multietape_modulaire.py (qui produit le .doctags source).

Usage :
    uv run python reordered_doctags.py --input data/output_files/MonDoc/MonDoc.doctags
    uv run python reordered_doctags.py --dotenv .env.test
"""
import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_log = logging.getLogger(__name__)

TAG_UL_CLOSE = "</unordered_list>"
_NO_X0 = 10**9  # valeur arbitraire x0 pour les blocs sans coordonnée horizontale = placés en dernier


# Modèle de données
@dataclass
class Block:
    raw: str
    y0: int | None
    x0: int | None
    is_list_item: bool = False


# Logique métier (fonctions pures)
def extract_xy0(s: str) -> tuple[int | None, int | None]:
    """
    Docstring for extract_xy0
    Extrait (x0, y0) depuis la première paire <loc_x0><loc_y0> trouvée dans s.

    :param s: Description
    :type s: str
    :return: Description
    :rtype: tuple[int | None, int | None]
    """
    match = re.search(r"<loc_(\d+)><loc_(\d+)>", s)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _collect_until(lines: list[str], start: int, closing_tag: str) -> tuple[list[str], int]:
    """
    Docstring for _collect_until
    Accumule les lignes de start jusqu'à closing_tag inclus. Retourne (parts, next_i).

    :param lines: Description
    :type lines: list[str]
    :param start: Description
    :type start: int
    :param closing_tag: Description
    :type closing_tag: str
    :return: Description
    :rtype: tuple[list[str], int]
    """
    parts = [lines[start]]
    i = start + 1
    while i < len(lines):
        parts.append(lines[i])
        if closing_tag in lines[i]:
            i += 1
            break
        i += 1
    return parts, i


def _parse_ordered_list(lines: list[str], i: int) -> tuple[Block, int]:
    """
    Docstring for _parse_ordered_list
    Parse un bloc <ordered_list>…</ordered_list> comme un seul Block. Retourne (Block, next_i).

    :param lines: Description
    :type lines: list[str]
    :param i: Description
    :type i: int
    :return: Description
    :rtype: tuple[Block, int]
    """
    parts, i = _collect_until(lines, i, "</ordered_list>")
    text = "\n".join(parts)
    x0, y0 = extract_xy0(text)
    return Block(raw=text, y0=y0, x0=x0, is_list_item=False), i


def _parse_unordered_list(lines: list[str], i: int) -> tuple[list[Block], int]:
    """
    Docstring for _parse_unordered_list
    Parse un bloc <unordered_list>…</unordered_list> en Blocks individuels. Retourne (blocks, next_i).

    :param lines: Description
    :type lines: list[str]
    :param i: Description
    :type i: int
    :return: Description
    :rtype: tuple[list[Block], int]
    """
    parts, i = _collect_until(lines, i, TAG_UL_CLOSE)
    ul_text = "\n".join(parts)
    blocks = []
    for item in re.findall(r"<list_item>.*?</list_item>", ul_text, flags=re.DOTALL):
        x0, y0 = extract_xy0(item)
        blocks.append(Block(raw=item.replace("\n", "").strip(), y0=y0, x0=x0, is_list_item=True))
    return blocks, i


def parse_blocks(content: str) -> list[Block]:
    """
    Docstring for parse_blocks
    - ordered_list  : traité comme un seul bloc pour éviter que </ordered_list> (y0=None) remonte avant ses items lors du tri.
    - unordered_list: items extraits individuellement (is_list_item=True) pour être triés, render_blocks les réenveloppe ensuite.

    :param content: Description
    :type content: str
    :return: Description
    :rtype: list[Block]
    """
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    blocks: list[Block] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if "<ordered_list>" in line:
            block, i = _parse_ordered_list(lines, i)
            blocks.append(block)
        elif "<unordered_list>" in line:
            new_blocks, i = _parse_unordered_list(lines, i)
            blocks.extend(new_blocks)
        else:
            x0, y0 = extract_xy0(line)
            blocks.append(Block(raw=line, y0=y0, x0=x0, is_list_item=False))
            i += 1

    return blocks


def split_pages(blocks: list[Block]) -> list[list[Block]]:
    """
    Docstring for split_pages
    Sépare une liste de blocs en pages d'après les balises <page_footer> et <page_break>.

    :param blocks: Description
    :type blocks: list[Block]
    :return: Description
    :rtype: list[list[Block]]
    """
    pages: list[list[Block]] = []
    current: list[Block] = []

    for block in blocks:
        current.append(block)
        if "<page_footer>" in block.raw or "<page_break>" in block.raw:
            pages.append(current)
            current = []

    if current:
        pages.append(current)

    return pages


def sort_page(blocks: list[Block]) -> list[Block]:
    """
    Docstring for sort_page
    Trie les blocs d'une page par y0 croissant, puis x0 croissant.
    Les blocs sans coordonnées (y0=None) sont placés en tête dans leur ordre d'origine.
    L'index d'origine sert de tiebreaker pour garantir un tri stable.

    :param blocks: Description
    :type blocks: list[Block]
    :return: Description
    :rtype: list[Block]
    """
    indexed = list(enumerate(blocks))
    no_pos  = [(i, b) for i, b in indexed if b.y0 is None]
    with_pos = [(i, b) for i, b in indexed if b.y0 is not None]
    with_pos.sort(key=lambda t: (t[1].y0, t[1].x0 if t[1].x0 is not None else _NO_X0, t[0]))
    return [b for _, b in no_pos] + [b for _, b in with_pos]


def render_blocks(blocks: list[Block]) -> str:
    """
    Docstring for render_blocks
    Convertit une liste de blocs triés en texte, en réenveloppant les list_item dans <unordered_list>.

    :param blocks: Description
    :type blocks: list[Block]
    :return: Description
    :rtype: str
    """
    out: list[str] = []
    in_ul = False

    for block in blocks:
        if block.is_list_item:
            if not in_ul:
                out.append("<unordered_list>")
                in_ul = True
            out.append(block.raw)
        else:
            if in_ul:
                out.append(TAG_UL_CLOSE)
                in_ul = False
            out.append(block.raw)

    if in_ul:
        out.append(TAG_UL_CLOSE)

    return "\n".join(out)


def reorder_doctags(input_path: Path, output_path: Path) -> None:
    """
    Docstring for reorder_doctags
    Lit input_path, retrie les blocs par y0/x0 page par page, écrit le résultat dans output_path.

    :param input_path: Description
    :type input_path: Path
    :param output_path: Description
    :type output_path: Path
    """

    content = input_path.read_text(encoding="utf-8")
    content = re.sub(r"</?doctag>\s*", "", content).strip()

    pages = split_pages(parse_blocks(content))
    result_pages = [render_blocks(sort_page(page)) for page in pages]

    final = "<doctag>\n" + "\n".join(result_pages) + "\n</doctag>\n"
    output_path.write_text(final, encoding="utf-8")
    _log.info("Doctags réordonné (%d page(s)) : %s", len(pages), output_path)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Réordonne les blocs d'un fichier .doctags par coordonnées y0/x0 (page par page). "
            "À lancer après pipeline_multietape_modulaire.py."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python reordered_doctags.py "
            "--input data/output_files/MonDoc/MonDoc.doctags\n"
            "  uv run python reordered_doctags.py "
            "--input data/output_files/MonDoc/MonDoc.doctags "
            "--output data/output_files/MonDoc/MonDoc_reordered.doctags\n"
            "  uv run python reordered_doctags.py --dotenv .env.test\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help=(
            "Chemin vers le fichier .doctags source. "
            "Si absent, résout data/output_files/<DOC_NAME>/<DOC_NAME>.doctags depuis l'environnement."
        ),
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Chemin du fichier .doctags réordonné en sortie. "
            "Défaut : même dossier que --input, suffixe _reordered ajouté au nom."
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
def _project_root() -> Path:
    """
    Docstring for _project_root
    
    :return: Description
    :rtype: Path
    """
    return Path(__file__).resolve().parent.parent


def resolve_input(args: argparse.Namespace) -> Path:
    """
    Docstring for resolve_input
    
    :param args: Description
    :type args: argparse.Namespace
    :return: Description
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    if args.dotenv:
        dotenv_path = args.dotenv.resolve()
        if not dotenv_path.exists():
            raise SystemExit(f"Erreur : fichier .env introuvable — {dotenv_path}")
        load_dotenv(dotenv_path=dotenv_path)
        _log.info("Environnement chargé depuis : %s", dotenv_path)
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if not doc_name:
        raise SystemExit(
            "Erreur : fournir --input <chemin>, ou --dotenv <fichier> avec DOC_NAME, "
            "ou définir la variable DOC_NAME dans l'environnement."
        )
    return _project_root() / "data" / "output_files" / doc_name / f"{doc_name}.doctags"


def resolve_output(args: argparse.Namespace, input_path: Path) -> Path:
    """
    Docstring for resolve_output
    
    :param args: Description
    :type args: argparse.Namespace
    :param input_path: Description
    :type input_path: Path
    :return: Description
    :rtype: Path
    """
    if args.output:
        return args.output.resolve()
    return input_path.parent / f"{input_path.stem}_reordered.doctags"


# Point d'entrée
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    input_path = resolve_input(args)
    if not input_path.exists():
        raise SystemExit(f"Erreur : fichier .doctags introuvable — {input_path}")

    output_path = resolve_output(args, input_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    _log.info("Entrée  : %s", input_path)
    _log.info("Sortie  : %s", output_path)

    try:
        reorder_doctags(input_path, output_path)
    except Exception:
        _log.exception("Erreur lors du réordonnancement de %s", input_path.name)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
