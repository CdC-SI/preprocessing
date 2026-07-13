"""
markdown_tables_to_jsonl.py — Exporte les tables Markdown (natives, pipe |col|col|)
d'un document corrigé (_final.md) en JSONL.

Deux sorties possibles, à partir du même parsing (cf. iter_blocks) :
  1. Traçabilité (toujours) : un fichier .jsonl par table détectée, dans un dossier séparé
     (par défaut : tables_markdown/ à côté du markdown source) — n'affecte jamais le
     markdown utilisé pour l'embedding.
  2. Embedding (--embed-output) : réécrit le document entier en remplaçant chaque table
     Markdown par ses lignes JSONL, pour un usage où l'on veut que l'embedding porte sur
     des tables structurées plutôt que sur du Markdown pipe — au prix du surcoût de tokens
     déjà mesuré (répétition des clés de colonne à chaque ligne, cf. comparaison baseline).

Contrairement à csv_to_jsonlines.py (qui convertit les CSV extraits par Docling
avant toute correction VLM), ce script lit les tables APRÈS correction VLM.

Gère l'artefact de frontière de page : quand la correction VLM (page par page) réémet
la ligne d'en-tête au début d'une nouvelle page sans nouvelle ligne de séparateur
(|---|---|), cette ligne est reconnue comme un doublon de l'en-tête et ignorée plutôt
que traitée comme une ligne de données.

Usage :
    uv run python markdown_tables_to_jsonl.py --markdown data/output_files_preprocessing/MonDoc/MonDoc_final.md
    uv run python markdown_tables_to_jsonl.py --dotenv .env.test --stage5 data/output_files_preprocessing
    uv run python markdown_tables_to_jsonl.py --markdown ... --output-dir data/output_files_preprocessing/MonDoc/tables_markdown
    uv run python markdown_tables_to_jsonl.py --markdown MonDoc_final.md --embed-output MonDoc_final_embed.md
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

import jsonlines

from utils.paths import project_root, resolve_doc_name

_log = logging.getLogger(__name__)

DEFAULT_STAGE5 = project_root() / "data" / "output_files_preprocessing"


# Parsing markdown → tables
def deduplicate_columns(columns: list[str]) -> list[str]:
    """Ajoute un suffixe numérique aux colonnes dupliquées (col, col_2, col_3…)."""
    counts: dict[str, int] = {}
    result = []
    for col in columns:
        if col not in counts:
            counts[col] = 1
            result.append(col)
        else:
            counts[col] += 1
            result.append(f"{col}_{counts[col]}")
    return result


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return len(s) > 1 and s.startswith("|") and s.endswith("|")


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator_line(line: str) -> bool:
    cells = _split_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-+:?", c) for c in cells)


def _is_table_start(lines: list[str], i: int) -> bool:
    """True si lines[i] est un en-tête immédiatement suivi de son séparateur |---|---|."""
    n = len(lines)
    return _is_table_line(lines[i]) and i + 1 < n and _is_table_line(lines[i + 1]) and _is_separator_line(lines[i + 1])


def _build_row(cells: list[str], header_keys: list[str], prev_row: dict | None) -> dict:
    """
    Construit le dict {colonne: valeur} d'une ligne de données. Forward-fill générique :
    une cellule vide est remplie avec la valeur de la même colonne à la ligne précédente
    (règle purement positionnelle, valable pour n'importe quelle table/colonne — pas de
    nom de colonne en dur). Reproduit le comportement standard d'"unmerge" des cellules
    fusionnées (rowspan) d'un tableau PDF, que la correction VLM en amont ne restitue pas
    de façon fiable.

    prev_row=None désactive le forward-fill pour cette ligne (cf. _extract_table_at :
    jamais appliqué à la dernière ligne d'un bloc — position où atterrissent les
    artefacts de coupure de page, où la ligne du dessus n'a aucun rapport réel).
    """
    row: dict = {}
    for idx, key in enumerate(header_keys):
        value = cells[idx] if idx < len(cells) else ""
        if not value and prev_row and prev_row.get(key):
            value = prev_row[key]
        row[key] = value
    return row


def _collect_raw_rows(lines: list[str], start: int, header_labels: list[str]) -> tuple[list[list[str]], int]:
    """Consomme les lignes de données brutes (cellules non fusionnées) à partir de start,
    jusqu'à une ligne non-tableau ou un nouveau couple (en-tête, séparateur). Ignore les
    lignes identiques à l'en-tête (doublon de frontière de page)."""
    n = len(lines)
    raw_rows: list[list[str]] = []
    j = start
    while j < n and _is_table_line(lines[j]) and not _is_table_start(lines, j):
        cells = _split_cells(lines[j])
        if cells != header_labels:
            raw_rows.append(cells)
        j += 1
    return raw_rows, j


def _extract_table_at(lines: list[str], i: int) -> tuple[list[dict], int] | None:
    """
    Si lines[i] démarre une table (en-tête + séparateur en i+1), consomme les lignes de
    données qui suivent — jusqu'à une ligne non-tableau ou un nouveau couple (en-tête,
    séparateur), qui marque le début d'une table adjacente sans ligne de séparation entre
    les deux.

    Forward-fill (cf. _build_row) appliqué à toutes les lignes SAUF la dernière du bloc :
    une coupure de page en plein milieu d'un groupe de lignes fusionnées peut laisser un
    "orphelin" en toute fin de bloc, juste avant qu'un nouvel en-tête ne redémarre pour un
    groupe totalement différent — dans ce cas, la ligne précédente n'a aucun rapport réel
    et un forward-fill y insérerait une donnée fausse plutôt qu'une cellule vide honnête.

    :return: (rows, prochain_index) si lines[i] démarre une table, sinon None
    """
    if not _is_table_start(lines, i):
        return None

    header_labels = _split_cells(lines[i])
    header_keys = deduplicate_columns(header_labels)
    raw_rows, next_i = _collect_raw_rows(lines, i + 2, header_labels)

    rows: list[dict] = []
    prev_row: dict | None = None
    last_idx = len(raw_rows) - 1
    for idx, cells in enumerate(raw_rows):
        row = _build_row(cells, header_keys, None if idx == last_idx else prev_row)
        rows.append(row)
        prev_row = row
    return rows, next_i


def iter_blocks(text: str):
    """
    Parcourt le texte une seule fois et produit une séquence de blocs :
    ("text", line) pour chaque ligne hors-table, ("table", rows) une fois par table
    Markdown native détectée (| col | col |), rows étant la liste de dict {colonne: valeur}
    de cette table.

    Point d'entrée unique partagé par parse_markdown_tables() (fichiers .jsonl séparés,
    traçabilité) et render_markdown_with_jsonl_tables() (document réécrit pour l'embedding) —
    pour éviter que les deux dérivent en cas de modification future du format des tables.

    :param text: contenu markdown à parser
    :return: générateur de tuples ("text", str) | ("table", list[dict])
    """
    lines = text.split("\n")
    i, n = 0, len(lines)
    while i < n:
        extracted = _extract_table_at(lines, i)
        if extracted is not None:
            rows, next_i = extracted
            if rows:
                yield "table", rows
            i = next_i
            continue
        yield "text", lines[i]
        i += 1


def parse_markdown_tables(text: str) -> list[list[dict]]:
    """Extrait les tables Markdown natives d'un texte. cf. iter_blocks()."""
    return [rows for kind, rows in iter_blocks(text) if kind == "table"]


def render_markdown_with_jsonl_tables(text: str) -> str:
    """
    Reconstruit le document en remplaçant chaque table Markdown native (| col | col |)
    par ses lignes JSONL équivalentes (une par ligne), en préservant tout le texte
    hors-table à l'identique. Réutilise iter_blocks() — donc, par construction, produit
    exactement les mêmes lignes que celles écrites dans <doc>-table-N.jsonl par
    write_tables_jsonl().

    Utilisé pour produire le contenu réellement embeddé (_final_embed.md) quand on veut
    que l'embedding porte sur des tables structurées plutôt que sur du Markdown pipe —
    au prix du surcoût de tokens déjà mesuré (répétition des clés de colonne à chaque ligne).

    :param text: contenu markdown source
    :return: contenu markdown avec les tables remplacées par du JSONL
    """
    out: list[str] = []
    for kind, value in iter_blocks(text):
        if kind == "table":
            out.extend(json.dumps(row, ensure_ascii=False) for row in value)
        else:
            out.append(value)
    return "\n".join(out)


def write_tables_jsonl(tables: list[list[dict]], output_dir: Path, doc_name: str) -> list[Path]:
    """Écrit une table par fichier : <doc_name>-table-1.jsonl, -table-2.jsonl, …"""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for idx, rows in enumerate(tables, start=1):
        path = output_dir / f"{doc_name}-table-{idx}.jsonl"
        with jsonlines.open(path, mode="w") as writer:
            for row in rows:
                writer.write(row)
        _log.info("Table %d : %d ligne(s) → %s", idx, len(rows), path.name)
        written.append(path)
    return written


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Exporte les tables Markdown natives d'un document corrigé (_final.md) en JSONL, "
            "pour traçabilité — n'affecte jamais le markdown utilisé pour l'embedding."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python markdown_tables_to_jsonl.py \\\n"
            "      --markdown data/output_files_preprocessing/MonDoc/MonDoc_final.md\n"
            "  uv run python markdown_tables_to_jsonl.py --dotenv .env.test --stage5 data/output_files_preprocessing\n"
        ),
    )
    parser.add_argument(
        "--doc-name",
        type=str,
        default=None,
        help="Nom du document sans extension. Si absent, résout DOC_NAME depuis --dotenv ou l'environnement.",
    )
    parser.add_argument(
        "--stage5",
        type=Path,
        default=DEFAULT_STAGE5,
        help=f"Racine de sortie du pipeline (contient <doc_name>/<doc_name>_final.md). Défaut : {DEFAULT_STAGE5}.",
    )
    parser.add_argument(
        "--markdown", "-m",
        type=Path,
        default=None,
        help="Chemin explicite vers le markdown à parser. Défaut : <stage5>/<doc_name>/<doc_name>_final.md.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help="Dossier de sortie des .jsonl. Défaut : <dossier du markdown>/tables_markdown/.",
    )
    parser.add_argument(
        "--embed-output",
        type=Path,
        default=None,
        help=(
            "Si fourni, écrit aussi le document entier avec les tables remplacées par du JSONL "
            "à ce chemin (ex. : <doc>_final_embed.md) — destiné à être utilisé comme source de "
            "l'embedding à la place du markdown natif."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger pour résoudre DOC_NAME (ex. : .env.test).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def resolve_markdown(args: argparse.Namespace, doc_name: str) -> Path:
    if args.markdown:
        return args.markdown.resolve()
    return args.stage5 / doc_name / f"{doc_name}_final.md"


def resolve_output_dir(args: argparse.Namespace, markdown_path: Path) -> Path:
    if args.output_dir:
        return args.output_dir.resolve()
    return markdown_path.parent / "tables_markdown"


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    doc_name = args.doc_name or resolve_doc_name(args, primary_flag="--doc-name")
    markdown_path = resolve_markdown(args, doc_name)
    if not markdown_path.exists():
        raise SystemExit(f"Erreur : fichier markdown introuvable — {markdown_path}")

    output_dir = resolve_output_dir(args, markdown_path)

    text = markdown_path.read_text(encoding="utf-8")
    tables = parse_markdown_tables(text)

    if not tables:
        _log.warning("Aucune table Markdown détectée dans %s", markdown_path)
        if args.embed_output:
            args.embed_output.parent.mkdir(parents=True, exist_ok=True)
            args.embed_output.write_text(text, encoding="utf-8")
            _log.info("Aucune table à convertir — copie inchangée → %s", args.embed_output)
        sys.exit(0)

    _log.info("%d table(s) détectée(s) dans %s", len(tables), markdown_path.name)
    write_tables_jsonl(tables, output_dir, doc_name)
    _log.info("Terminé. Sortie : %s", output_dir)

    if args.embed_output:
        embed_text = render_markdown_with_jsonl_tables(text)
        args.embed_output.parent.mkdir(parents=True, exist_ok=True)
        args.embed_output.write_text(embed_text, encoding="utf-8")
        _log.info(
            "Document réécrit pour l'embedding (%d → %d caractères) → %s",
            len(text), len(embed_text), args.embed_output,
        )


if __name__ == "__main__":
    main()
