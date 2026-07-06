"""
load_jsonline_doctags_modulaire.py — Injection des tables JSONL dans un fichier .doctags.

Remplace chaque balise <otsl>…</otsl> du .doctags par un bloc <text> contenant
le contenu JSONL de la table correspondante (ordre d'apparition = ordre de tri des fichiers).

Se lance après :
  - reordered_doctags.py   → produit <DOC_NAME>_reordered.doctags
  - csv_to_jsonlines_modulaire.py → produit les .jsonl dans tables/

Usage :
    uv run python load_jsonline_doctags_modulaire.py \\
        --doctags  data/output_files_preprocessing/MonDoc/MonDoc_reordered.doctags \\
        --tables-dir data/output_files_preprocessing/MonDoc/tables
    uv run python load_jsonline_doctags_modulaire.py --dotenv .env.test
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

from utils.paths import project_root, resolve_doc_name

_log = logging.getLogger(__name__)


# Logique métier (fonctions pures)
def load_jsonl_rows(jsonl_path: Path) -> list[dict]:
    """Charge toutes les lignes d'un fichier JSONL et les retourne comme liste de dicts."""
    rows = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def jsonl_rows_to_block(rows: list[dict]) -> str:
    """Convertit une liste de dicts en bloc texte JSONL (une ligne par dict)."""
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


_TABLE_COORDS_RE = re.compile(r"_page(\d+)_x(\d+)_y(\d+)_x(\d+)_y(\d+)")

TableCoords = tuple[int, int, int, int, int]  # (page, x0, y0, x1, y1)


def _parse_table_coords(filename: str) -> TableCoords | None:
    """Extrait (page, x0, y0, x1, y1) d'un nom de fichier produit par
    pipeline_multietape_modular.export_tables() (page 1-indexée, coordonnées doctags 0-500).
    Retourne None pour un fichier antérieur à ce correctif (pas de coordonnées dans le nom) —
    déclenche le repli sur le matching par ordre de fichier dans replace_otsl_with_jsonl.
    """
    m = _TABLE_COORDS_RE.search(filename)
    if not m:
        return None
    x0, y0, x1, y1 = (int(g) for g in m.groups()[1:])
    return (int(m.group(1)), x0, y0, x1, y1)


def _find_otsl_blocks(content: str) -> list[tuple[re.Match, TableCoords]]:
    """Localise chaque bloc <otsl>…</otsl> avec ses coordonnées (page, x0, y0, x1, y1).

    Page déduite du nombre de <page_footer> rencontrés avant le bloc (1-indexée, même
    convention que pipeline_multietape_modular.export_tables — table.prov[0].page_no).
    Coordonnées lues directement dans le tag d'ouverture <otsl><loc_x0><loc_y0><loc_x1><loc_y1>.
    """
    footer_offsets = [m.start() for m in re.finditer(r"<page_footer>", content)]
    otsl_pattern = re.compile(
        r"<otsl><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>.*?</otsl>", re.DOTALL
    )

    blocks: list[tuple[re.Match, TableCoords]] = []
    for m in otsl_pattern.finditer(content):
        page = 1 + sum(1 for fo in footer_offsets if fo < m.start())
        x0, y0, x1, y1 = (int(g) for g in m.groups())
        blocks.append((m, (page, x0, y0, x1, y1)))
    return blocks


def replace_otsl_with_jsonl(
    doctags_path: Path,
    tables_dir: Path,
    output_path: Path,
) -> int:
    """Remplace les balises <otsl>…</otsl> par le contenu JSONL de la table correspondante,
    matchée par coordonnées (page, x0, y0, x1, y1) — jamais par ordre de fichier — pour rester
    correct même si reordered_doctags_modular.py a changé l'ordre relatif des tables sur une
    page (son rôle même). Retombe sur l'ordre de fichier historique (bogué si l'ordre a
    changé, ou si un document compte 10+ tables — tri alphabétique de "table-10" avant
    "table-2") uniquement si les JSONL présents datent d'avant ce correctif et ne portent pas
    de coordonnées dans leur nom.

    Retourne le nombre de remplacements effectués.
    Si aucun JSONL ou aucune balise <otsl> n'est trouvé, copie le fichier source à l'identique
    (passthrough) pour que l'étape suivante du pipeline reçoive toujours un fichier valide.
    """
    content = doctags_path.read_text(encoding="utf-8")

    # Charger les JSONL disponibles
    jsonl_files = sorted(tables_dir.glob("*.jsonl"))
    if not jsonl_files:
        _log.warning("Aucun fichier JSONL dans %s — fichier copié sans modification.", tables_dir)
        output_path.write_text(content, encoding="utf-8")
        return 0

    _log.info("%d fichier(s) JSONL trouvé(s) dans : %s", len(jsonl_files), tables_dir)
    tables_in_order: list[tuple[str, list[dict]]] = []
    tables_by_coords: dict[TableCoords, tuple[str, list[dict]]] = {}
    for jsonl_path in jsonl_files:
        rows = load_jsonl_rows(jsonl_path)
        if not rows:
            continue
        tables_in_order.append((jsonl_path.name, rows))
        coords = _parse_table_coords(jsonl_path.name)
        if coords:
            tables_by_coords[coords] = (jsonl_path.name, rows)
        _log.info("  • %s : %d ligne(s)", jsonl_path.name, len(rows))

    if not tables_in_order:
        _log.warning("Tous les fichiers JSONL sont vides — fichier copié sans modification.")
        output_path.write_text(content, encoding="utf-8")
        return 0

    # Localiser les balises <otsl>
    otsl_blocks = _find_otsl_blocks(content)
    if not otsl_blocks:
        _log.warning("Aucune balise <otsl> dans %s — fichier copié sans modification.", doctags_path.name)
        output_path.write_text(content, encoding="utf-8")
        return 0

    use_coords = len(tables_by_coords) == len(tables_in_order)
    if not use_coords:
        _log.warning(
            "JSONL sans coordonnées détecté(s) (généré avant ce correctif) — repli sur le "
            "matching par ordre de fichier, potentiellement incorrect si l'ordre des tables "
            "a changé. Ré-exécuter les étapes 1/2/4 pour régénérer des JSONL avec coordonnées."
        )

    if len(otsl_blocks) != len(tables_in_order):
        _log.warning(
            "%d bloc(s) <otsl> vs %d table(s) JSONL — remplacement au mieux.",
            len(otsl_blocks), len(tables_in_order),
        )

    # Remplacement par découpage/jointure (pas de mutation de chaîne en place — les positions
    # des matches restent valides puisqu'on ne réécrit jamais `content`)
    result_parts: list[str] = []
    cursor = 0
    n_replaced = 0

    for idx, (match, coords) in enumerate(otsl_blocks):
        table = tables_by_coords.get(coords) if use_coords else None
        if table is None:
            if idx < len(tables_in_order):
                table = tables_in_order[idx]
            else:
                _log.warning(
                    "Pas de table JSONL pour le bloc <otsl> n°%d (page=%d) — ignoré.",
                    idx + 1, coords[0],
                )
                continue

        jsonl_name, rows = table
        jsonl_block = jsonl_rows_to_block(rows)
        new_tag = f"<text>\n{jsonl_block}\n</text>"

        result_parts.append(content[cursor:match.start()])
        result_parts.append(new_tag)
        cursor = match.end()
        n_replaced += 1

        _log.info(
            "  Bloc <otsl> %d/%d (page=%d) remplacé — %s (%d ligne(s), %d chars)",
            idx + 1, len(otsl_blocks), coords[0], jsonl_name, len(rows), len(jsonl_block),
        )

    result_parts.append(content[cursor:])
    result = "".join(result_parts)

    output_path.write_text(result, encoding="utf-8")
    _log.info("Doctags enrichi sauvegardé : %s", output_path)
    return n_replaced


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Injecte les tables JSONL dans un .doctags en remplaçant les balises <otsl>. "
            "À lancer après reordered_doctags.py et csv_to_jsonlines_modulaire.py."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python load_jsonline_doctags_modulaire.py \\\n"
            "      --doctags  data/output_files_preprocessing/MonDoc/MonDoc_reordered.doctags \\\n"
            "      --tables-dir data/output_files_preprocessing/MonDoc/tables\n"
            "  uv run python load_jsonline_doctags_modulaire.py --dotenv .env.test\n"
        ),
    )
    parser.add_argument(
        "--doctags", "-d",
        type=Path,
        default=None,
        help=(
            "Fichier .doctags source (produit par reordered_doctags.py). "
            "Si absent, résout data/output_files_preprocessing/<DOC_NAME>/<DOC_NAME>_reordered.doctags."
        ),
    )
    parser.add_argument(
        "--tables-dir", "-t",
        type=Path,
        default=None,
        help=(
            "Dossier contenant les fichiers .jsonl (produits par csv_to_jsonlines_modulaire.py). "
            "Si absent, résout data/output_files_preprocessing/<DOC_NAME>/tables."
        ),
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help=(
            "Fichier .doctags enrichi en sortie. "
            "Défaut : même dossier que --doctags, suffixe _with_tables ajouté au nom."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger pour résoudre DOC_NAME (ex. : .env.test). Ignoré si --doctags est fourni.",
    )
    return parser.parse_args()


def resolve_doctags(args: argparse.Namespace) -> Path:
    if args.doctags:
        return args.doctags.resolve()
    doc_name = resolve_doc_name(args, primary_flag="--doctags")
    return project_root() / "data" / "output_files_preprocessing" / doc_name / f"{doc_name}_reordered.doctags"


def resolve_tables_dir(args: argparse.Namespace, doctags_path: Path) -> Path:
    if args.tables_dir:
        return args.tables_dir.resolve()
    # Dérive depuis le dossier parent du doctags (structure standard)
    return doctags_path.parent / "tables"


def resolve_output(args: argparse.Namespace, doctags_path: Path) -> Path:
    if args.output:
        return args.output.resolve()
    return doctags_path.parent / f"{doctags_path.stem}_with_tables.doctags"


# Point d'entrée
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    doctags_path = resolve_doctags(args)
    if not doctags_path.exists():
        raise SystemExit(f"Erreur : fichier .doctags introuvable — {doctags_path}")

    tables_dir = resolve_tables_dir(args, doctags_path)
    output_path = resolve_output(args, doctags_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    _log.info("Doctags source  : %s", doctags_path)
    _log.info("Dossier tables  : %s", tables_dir)
    _log.info("Sortie          : %s", output_path)

    if not tables_dir.exists():
        _log.warning("Dossier tables introuvable (%s) — fichier copié sans modification.", tables_dir)
        output_path.write_text(doctags_path.read_text(encoding="utf-8"), encoding="utf-8")
        sys.exit(0)

    try:
        n = replace_otsl_with_jsonl(doctags_path, tables_dir, output_path)
    except Exception:
        _log.exception("Erreur lors de l'injection des tables dans %s", doctags_path.name)
        sys.exit(1)

    _log.info("Terminé — %d remplacement(s) <otsl> effectué(s).", n)
    sys.exit(0)


if __name__ == "__main__":
    main()
